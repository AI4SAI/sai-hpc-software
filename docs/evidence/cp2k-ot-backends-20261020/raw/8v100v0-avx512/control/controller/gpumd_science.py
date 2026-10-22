#!/usr/bin/env python3
"""Trusted GPUMD numerical/parity/portability and same-allocation microbenchmark.

No upstream Python/shell test driver is executed on the host. Preparation and
packaging stay in the build overlay; acceptance runs in a read-only candidate
SIF with only its work directory writable.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import statistics
import subprocess
import time

from export_native import (canonical, inventory, read_installed_manifests, write_manifests)
from native_module import validate_native_entry
from release_contract import validate_identity

COMMANDS = ("gpumd", "nep", "gnep")
CUDA = "/opt/devtools/nvidia/cuda-12.9.1"
GCC = "/opt/devtools/gcc/13.3.0"
DEEPMD = "/opt/apps/conda_env/deepmd-kit-3.2.0"
PLUMED = "/opt/apps/plumed/plumed-2.10.1"
NVHPC = "/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3"
NCCL = "/opt/devtools/nvidia/nccl_2.29.3_cuda12.9_sai_v2.29.3-1-sai.2"
MODULE_ROOT = "/opt/modules/modulefiles/devtools"
BLAS = "/usr/lib/x86_64-linux-gnu/blas"
LAPACK = "/usr/lib/x86_64-linux-gnu/lapack"
MODULES = ("cuda/12.9.1", "nvhpc/26.3-gnu-cuda12-tuned",
           "openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto", "gcc/13.3.0")
SITE_LIBRARIES = {
    "libdeepmd_cc.so": DEEPMD + "/lib/libdeepmd_cc.so",
    "libdeepmd_c.so": DEEPMD + "/lib/libdeepmd_c.so",
    "libplumed.so": PLUMED + "/lib/libplumed.so",
    "libplumedKernel.so": PLUMED + "/lib/libplumedKernel.so",
}
THREADS = {"OMP_NUM_THREADS": "2", "DP_INTRA_OP_PARALLELISM_THREADS": "1",
           "DP_INTER_OP_PARALLELISM_THREADS": "1"}


def _unique(values):
    return list(dict.fromkeys(values))


def _path(value):
    if (not isinstance(value, str) or not value.startswith("/") or
            str(PurePosixPath(value)) != value or ".." in value.split("/") or
            value.startswith("//") or ":" in value or "\\" in value or
            any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("dependency path must be an absolute canonical literal")
    return value


def _beneath(path, root):
    return path == root or path.startswith(root + "/")


def parse_ldd(report):
    """Parse a successful dynamic ELF ldd report, rejecting partial/missing data."""
    if not isinstance(report, str) or not report.strip():
        raise ValueError("empty ldd report")
    paths = []
    for line in report.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.fullmatch(r"linux-vdso\.so\.\d+ \(0x[0-9a-fA-F]+\)", line):
            continue
        match = re.fullmatch(r"(?:[^\s/]+ => )?(/[^\s]+) \(0x[0-9a-fA-F]+\)", line)
        if not match:
            raise ValueError("unresolved or malformed ldd report: " + line)
        paths.append(_path(match[1]))
    if not paths:
        raise ValueError("ldd did not resolve any libraries")
    return _unique(paths)


def _site_roots(identity):
    mpi = "/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-" + identity["dependency_isa"]
    return [CUDA, GCC, DEEPMD, PLUMED, mpi, NVHPC, NCCL, MODULE_ROOT, BLAS, LAPACK]


def build_native_entry(identity, prefix, environment, ldd_reports, executables=COMMANDS):
    """Pure constructor from a canonical identity and observed build evidence.

    ``ldd_reports`` maps all three installed commands and each SITE_LIBRARIES
    basename to its complete ldd stdout. PLUMED's MPI/HPCX dependencies are real
    transitive requirements, even though GPUMD itself is not an MPI program.
    The versioned site MPI module is auto-ISA; its *resolved* root is separately
    checked against the identity's dependency_isa and recorded in this entry.
    """
    identity = validate_identity(identity)
    if identity["software"] != "gpumd" or str(prefix) != identity["install_prefix"]:
        raise ValueError("GPUMD prefix must match its canonical release identity")
    prefix = _path(str(prefix))
    if (not isinstance(environment, dict) or
            any(not isinstance(k, str) or not isinstance(v, str) for k, v in environment.items())):
        raise ValueError("environment must map strings to strings")
    if tuple(executables) != COMMANDS:
        raise ValueError("delivery requires exactly the installed gpumd, nep and gnep commands")
    if not isinstance(ldd_reports, dict) or set(ldd_reports) != set(COMMANDS) | set(SITE_LIBRARIES):
        raise ValueError("complete command and site-library ldd evidence is required")
    roots = _site_roots(identity)
    mpi = roots[4]
    expected = {"CUDA_HOME": CUDA, "CUDA_PATH": CUDA, "DEEPMD_ROOT": DEEPMD,
                "PLUMED_KERNEL": SITE_LIBRARIES["libplumedKernel.so"],
                "DP_CUDA_INFER": "2", **THREADS}
    for key, value in expected.items():
        if environment.get(key) != value:
            raise ValueError(f"verified site environment differs at {key}")
    for key, value in {"OPAL_PREFIX": mpi, "MPI_HOME": mpi, "NVHPC_ROOT": NVHPC,
                       "NCCL_ROOT": NCCL, "CUDACXX": CUDA + "/bin/nvcc",
                       "GPUMD_SRC": prefix + "/share/gpumd/src"}.items():
        if key in environment and environment[key] != value:
            raise ValueError(f"resolved site environment differs at {key}")
    loaded = environment.get("LOADEDMODULES", "").split(":")
    if (len(set(loaded)) != len(loaded) or not set(MODULES) <= set(loaded) or
            set(loaded) - set(MODULES) - {"deepmd-kit/3.2.0"}):
        raise ValueError("unrecorded or missing exact infrastructure module")

    # PATH is deliberately not copied. Reject accidentally sourced build
    # paths anyway, then expose only commands from infrastructure modules and
    # this install. DeepMD's extra site-LAMMPS bin directory is not a dependency.
    for value in environment.get("PATH", "").split(":"):
        _path(value)
        if any(_beneath(value, root) for root in ("/workspace", "/control", "/input", "/home", "/tmp")):
            raise ValueError("build or snapshot path leaked into runtime PATH")

    def approved(value):
        _path(value)
        if not any(_beneath(value, root) for root in [prefix, *roots, "/usr", "/lib", "/lib64"]):
            raise ValueError("unrecorded native library path: " + value)
        return value

    library_paths = _unique(approved(value) for value in environment.get("LD_LIBRARY_PATH", "").split(":"))
    for required in (DEEPMD + "/lib", PLUMED + "/lib", BLAS, LAPACK, mpi + "/lib"):
        if required not in library_paths:
            raise ValueError("required site library directory is absent: " + required)
    resolved = {name: [approved(path) for path in parse_ldd(report)]
                for name, report in ldd_reports.items()}
    # Complete ldd can resolve via an ELF RPATH, not LD_LIBRARY_PATH. Preserve
    # its real parent too, so downstream runtime dlopen can find the same ABI.
    library_paths = _unique([*library_paths,
                             *(str(PurePosixPath(path).parent)
                               for paths in resolved.values() for path in paths)])
    if not any(_beneath(path, mpi) for paths in resolved.values() for path in paths):
        raise ValueError("PLUMED MPI resolution is missing from ldd evidence")
    settings = {**expected, "GPUMD_ROOT": prefix,
                "GPUMD_SRC": prefix + "/share/gpumd/src", "CUDACXX": CUDA + "/bin/nvcc",
                "PLUMED_ROOT": PLUMED}
    entry = {"identity": identity, "commands": {name: "bin/" + name for name in COMMANDS},
             "external_roots": roots,
             "runtime": {"modules": list(MODULES),
                         "prepend": {"MODULEPATH": [MODULE_ROOT], "LD_LIBRARY_PATH": library_paths},
                         "set": settings}}
    # Do not weaken the public whitelist for unsupported GPUMD variables.
    return validate_native_entry(entry)


def _regular(path, *, executable=False):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or (executable and info.st_mode & 0o111 != 0o111):
        raise ValueError("installed command/metadata is not a regular readable executable: " + str(path))
    return path


BASELINE = Path("/opt/apps/gpumd/GPUMD-5.8")
TRAIN_CONFIG = """type 2 Te Pb
version 4
cutoff 8 4
n_max 6 6
basis_size 6 6
l_max 4 2 0
neuron 30
population 10
generation 2
output_interval 1
"""
STATIC_RUN = "potential nep.txt\nvelocity 1\nensemble nve\ntime_step 0\ndump_xyz 1 dump.xyz force precision double\nrun 1\n"


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for part in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def first_frame(path):
    lines = path.read_text().splitlines()
    return "\n".join(lines[:int(lines[0]) + 2]) + "\n"


def prepare(source, destination):
    """Package inputs and gold output from the exact resolved source revision."""
    destination.mkdir(parents=True)
    static = destination / "static"
    static.mkdir()
    shutil.copyfile(source / "examples/gpumd_static/model.xyz", static / "model.xyz")
    shutil.copyfile(source / "examples/gpumd_static/dump.xyz", static / "gold.xyz")
    shutil.copyfile(source / "examples/nep_train/nep.txt", static / "nep.txt")
    (static / "run.in").write_text(STATIC_RUN)
    prediction = destination / "prediction"
    prediction.mkdir()
    for name in ("nep.txt", "nep.in", "train.xyz"):
        shutil.copyfile(source / "examples/nep_prediction" / name, prediction / name)
    for name in ("energy_train.out", "force_train.out", "virial_train.out"):
        shutil.copyfile(source / "examples/nep_prediction" / name, prediction / f"gold-{name}")
    training = destination / "training"
    training.mkdir()
    for name in ("nep.txt", "nep.restart"):
        shutil.copyfile(source / "examples/nep_train" / name, training / name)
    (training / "train.xyz").write_text(first_frame(source / "examples/nep_train/train.xyz"))
    (training / "nep.in").write_text(TRAIN_CONFIG)
    gradient = destination / "gnep"
    gradient.mkdir()
    shutil.copyfile(source / "tests_pytest/fixtures/training/train.xyz", gradient / "train.xyz")
    (gradient / "gnep.in").write_text("type 3 Ba Ti O\ncutoff 8 4\nn_max 4 4\nbasis_size 8 8\nl_max 4\nneuron 30\nlambda_v 0\nbatch 4\nepoch 2\nseed 20260831\n")
    deepmd = destination / "deepmd"
    deepmd.mkdir()
    shutil.copyfile(source / "examples/gpumd_dp_pytorch/model.xyz", deepmd / "model.xyz")
    (deepmd / "dp_settings.txt").write_text("dp 1 Cu\n")
    (deepmd / "run.in").write_text(STATIC_RUN.replace("potential nep.txt", "potential dp_settings.txt frozen_model.pth"))
    throughput = destination / "throughput"
    throughput.mkdir()
    shutil.copyfile(static / "nep.txt", throughput / "nep.txt")
    # Replicate the real 250-atom model, retaining its triclinic lattice.
    rows = (static / "model.xyz").read_text().splitlines()
    lattice = [float(x) for x in re.search(r'Lattice="([^"]+)"', rows[1]).group(1).split()]
    atoms = [row.split() for row in rows[2:2 + int(rows[0])]]
    replicated = []
    for a in range(2):
        for b in range(2):
            for c in range(2):
                shift = [a * lattice[k] + b * lattice[k + 3] + c * lattice[k + 6] for k in range(3)]
                for atom in atoms:
                    replicated.append(atom[0] + " " + " ".join(str(float(atom[k + 1]) + shift[k]) for k in range(3)))
    (throughput / "model.xyz").write_text(str(len(replicated)) + '\npbc="T T T" Lattice="' +
        " ".join(str(2 * value) for value in lattice) + '" Properties=species:S:1:pos:R:3\n' + "\n".join(replicated) + "\n")
    (throughput / "run.in").write_text("potential nep.txt\nvelocity 300\nensemble nve\ntime_step 0.5\ndump_thermo 100\nrun 1000\n")


def inspect(prefix, identity=None, environment=None):
    """ldd is supplemented with ELF NEEDED and real JIT-resource checks."""
    expected = {path.name for path in (BASELINE / "bin").iterdir() if path.is_file() and os.access(path, os.X_OK)}
    installed = set((prefix / "share/sai/executables.txt").read_text().split())
    if not expected <= installed or not {"gpumd", "nep", "gnep"} <= installed:
        raise ValueError("installed executable set is below the site baseline")
    report = {}
    for name in sorted(installed):
        binary = prefix / "bin" / name
        dynamic = subprocess.check_output(["readelf", "-d", binary], text=True)
        dependencies = subprocess.check_output(["ldd", binary], text=True)
        if "not found" in dependencies:
            raise ValueError(f"{name}: missing shared libraries")
        # __FILE__ strings are debug provenance, not loader dependencies.
        if re.search(r"/workspace|/control|/home/[^/]+/sai-hpc-software", dynamic):
            raise ValueError(f"{name}: nonportable loader path")
        needed = set(re.findall(r"\(NEEDED\).*\[(.*?)\]", dynamic))
        if (BASELINE / "bin" / name).exists():
            baseline_dynamic = subprocess.check_output(["readelf", "-d", BASELINE / "bin" / name], text=True)
            baseline_needed = set(re.findall(r"\(NEEDED\).*\[(.*?)\]", baseline_dynamic))
            if not baseline_needed <= needed:
                raise ValueError(f"{name}: baseline NEEDED dependency lost: {baseline_needed - needed}")
        if name == "gpumd" and not {"libdeepmd_cc.so", "libcublas.so.12", "libcusolver.so.11", "libcufft.so.11"} <= needed:
            raise ValueError("GPUMD baseline CUDA/DeepMD features missing")
        report[name] = {"sha256": sha(binary), "needed": sorted(needed), "readelf": dynamic, "ldd": dependencies}
    for relative in ("main_nep/nep_specialized.cu", "utilities/nep_utilities.cuh"):
        if not (prefix / "share/gpumd/src" / relative).is_file():
            raise ValueError("missing installed NEP runtime specialization source")
    copied = prefix / "share/sai/portability"
    energy, force = xyz(copied / "dump.xyz")
    gold_e, gold_f = xyz(copied / "gold.xyz")
    compare([[energy]], [[gold_e]], 1e-3, "installed copied-prefix energy")
    compare(force, gold_f, 1e-4, "installed copied-prefix force")
    jit = (copied / "nep.log").read_text()
    if len(numbers(copied / "loss.out")) != 2 or "Compile specialized NEP training kernels" not in jit or "specialization disabled" in jit:
        raise ValueError("installed copied-prefix JIT evidence is incomplete")
    if identity is not None:
        # The existing inspection is the last build step. Package declarative
        # runtime data with the shared writer; its native modules are inventoried
        # inside this same prefix, with no GPUMD-specific format or naming.
        identity = validate_identity(identity)
        if (identity["software"] != "gpumd" or str(prefix) != identity["install_prefix"] or
                prefix.resolve(strict=True) != prefix or prefix.is_symlink()):
            raise ValueError("GPUMD prefix must match its canonical release identity")
        environment = dict(os.environ if environment is None else environment)
        for compiler, expected in (("nvcc", CUDA + "/bin/nvcc"), ("gcc", GCC + "/bin/gcc")):
            if shutil.which(compiler, path=environment.get("PATH")) != expected:
                raise ValueError("JIT compiler differs from verified site dependency: " + compiler)
        ldd_reports = {name: result["ldd"] for name, result in report.items()}
        for name, path in SITE_LIBRARIES.items():
            if not Path(path).is_file():
                raise ValueError("missing site dependency: " + path)
            ldd_reports[name] = subprocess.check_output(
                ["/usr/bin/ldd", path], text=True, env=dict(environment, LC_ALL="C"),
                stderr=subprocess.STDOUT)
        for name in COMMANDS:
            _regular(prefix / "bin" / name, executable=True)
        for relative in ("main_nep/nep_specialized.cu", "utilities/nep_utilities.cuh"):
            _regular(prefix / "share/gpumd/src" / relative)
        entry = build_native_entry(identity, prefix, environment, ldd_reports,
                                   _regular(prefix / "share/sai/executables.txt").read_text().split())
        for path in [*entry["external_roots"], *entry["runtime"]["prepend"]["LD_LIBRARY_PATH"]]:
            if not Path(path).is_dir():
                raise ValueError("recorded native dependency does not exist: " + path)
        evidence = {"schema": 1, "modules": entry["runtime"]["modules"],
                    "external_roots": entry["external_roots"],
                    "resolved_libraries": {name: parse_ldd(value) for name, value in ldd_reports.items()},
                    "ldd": ldd_reports, "scientific_acceptance": False}
        for name, value in (("release-identity.json", identity), ("native-entry.json", entry),
                            ("native-dependencies.json", evidence)):
            path = prefix / "share/sai" / name
            if path.exists() or path.is_symlink():
                if json.loads(_regular(path).read_text()) != value:
                    raise ValueError("refusing to replace different installed metadata: " + str(path))
            else:
                with path.open("x") as output:
                    output.write(canonical(value))
                path.chmod(0o444)
        write_manifests([entry])
    else:
        entry = validate_native_entry(json.loads(_regular(prefix / "share/sai/native-entry.json").read_text()))
        if entry["identity"]["install_prefix"] != str(prefix):
            raise ValueError("installed native entry differs from its prefix")
        if read_installed_manifests([entry]) != inventory([entry]):
            raise ValueError("installed native manifest differs from the complete installation")
    return report


def portable_check(prefix, destination):
    """Copy the complete prefix INSIDE the build overlay and run from elsewhere.

    This deliberately does not copy to a writable host installation directory.
    Both the shell environment and NEP's runtime compiler must find resources
    through the copied prefix, not the original build tree.
    """
    if destination != Path("/workspace/gpumd-portability"):
        raise ValueError("portable installation probe is restricted to its overlay path")
    copied = destination / "copied-opt/gpumd"
    shutil.copytree(prefix, copied)
    case = destination / "unrelated-cwd/static"
    shutil.copytree(copied / "share/sai/cases/static", case)
    training = destination / "unrelated-cwd/training"
    shutil.copytree(copied / "share/sai/cases/training", training)
    with (training / "nep.in").open("a") as stream:
        stream.write("nep_compile on\n")
    for working, executable in ((case, "gpumd"), (training, "nep")):
        with (working / "run.log").open("w") as log:
            subprocess.run(["/usr/bin/bash", "--noprofile", "--norc", "-c",
                'source "$1/share/sai/runtime-env.sh"; '
                'test "$GPUMD_SRC" = "$1/share/gpumd/src"; '
                'test "$(command -v "$2")" = "$1/bin/$2"; exec "$2"',
                "bash", str(copied), executable], cwd=working, check=True, timeout=180,
                stdout=log, stderr=subprocess.STDOUT)
    energy, force = xyz(case / "dump.xyz")
    gold_e, gold_f = xyz(case / "gold.xyz")
    loss = numbers(training / "loss.out")
    jit = (training / "run.log").read_text()
    if len(loss) != 2 or "Compile specialized NEP training kernels" not in jit or "specialization disabled" in jit:
        raise ValueError("copied-prefix NEP JIT failed or used fallback")
    proof = {"verified": True, "copied_inside_overlay": True, "unrelated_working_directory": True,
             "relative_jit_sources": True,
             "energy_error_ev": compare([[energy]], [[gold_e]], 1e-3, "copied-prefix energy"),
             "force_error": compare(force, gold_f, 1e-4, "copied-prefix forces"),
             "nep_generations": len(loss)}
    evidence = prefix / "share/sai/portability"
    evidence.mkdir()
    for name, source in (("gpumd.log", case / "run.log"), ("nep.log", training / "run.log"),
                         ("dump.xyz", case / "dump.xyz"), ("gold.xyz", case / "gold.xyz"),
                         ("loss.out", training / "loss.out")):
        shutil.copyfile(source, evidence / name)
    (evidence / "proof.json").write_text(json.dumps(proof, sort_keys=True) + "\n")
    print(json.dumps(proof, sort_keys=True))


def numbers(path):
    values = [[float(value) for value in line.split()] for line in path.read_text().splitlines()
              if line.strip() and not line.startswith("#")]
    if not values or not all(math.isfinite(value) for row in values for value in row):
        raise ValueError(f"missing/nonfinite numeric result: {path}")
    return values


def compare(actual, expected, tolerance, label):
    if len(actual) != len(expected) or any(len(a) != len(b) for a, b in zip(actual, expected)):
        raise ValueError(f"{label}: result dimensions changed")
    if not actual or any(not row for row in actual) or not all(
            math.isfinite(value) for matrix in (actual, expected) for row in matrix for value in row):
        raise ValueError(f"{label}: empty or nonfinite numeric data")
    error = max(abs(a - b) for row, ref in zip(actual, expected) for a, b in zip(row, ref))
    if not math.isfinite(error) or error > tolerance:
        raise ValueError(f"{label}: error {error} > {tolerance}")
    return error


def xyz(path):
    lines = path.read_text().splitlines()
    count = int(lines[0])
    energy = float(re.search(r"\benergy=(\S+)", lines[1]).group(1))
    rows = [line.split() for line in lines[2:count + 2]]
    forces = [[float(x) for x in row[-3:]] for row in rows]
    if len(forces) != count or not math.isfinite(energy) or not all(math.isfinite(value) for row in forces for value in row):
        raise ValueError("invalid XYZ result")
    return energy, forces


# A numerical derivative uses the same frozen GNEP model, but does not trust its
# analytical force implementation. Six predictions cover xyz for the first atom
# of each of the four independent 40-atom frames.
GNEP_DISPLACEMENT = 0.025
GNEP_FORCE_TOLERANCE = 0.02
MIN_ENGINE_SECONDS = 5.0


def displaced_frames(text, axis, displacement):
    lines = text.splitlines()
    cursor = 0
    counts = []
    while cursor < len(lines):
        count = int(lines[cursor])
        if count <= 0 or cursor + count + 2 > len(lines):
            raise ValueError("invalid GNEP training frame")
        match = re.search(r'Properties=([^\s]+)', lines[cursor + 1])
        if not match:
            raise ValueError("GNEP frame lacks explicit XYZ properties")
        fields = match.group(1).strip('"').split(":")
        offset = 0
        positions = None
        for index in range(0, len(fields), 3):
            width = int(fields[index + 2])
            if fields[index] == "pos" and fields[index + 1:index + 3] == ["R", "3"]:
                positions = offset
            offset += width
        if positions is None:
            raise ValueError("GNEP frame lacks Cartesian positions")
        atom = lines[cursor + 2].split()
        atom[positions + axis] = format(float(atom[positions + axis]) + displacement, ".16g")
        lines[cursor + 2] = " ".join(atom)
        counts.append(count)
        cursor += count + 2
    if counts != [40] * 4:
        raise ValueError("GNEP numerical derivative requires four 40-atom frames")
    return "\n".join(lines) + "\n"


def check_gnep_forces(task):
    reference = task / "gnep-prediction-candidate"
    forces = numbers(reference / "force_train.out")
    if len(forces) != 160 or any(len(row) != 6 for row in forces):
        raise ValueError("GNEP analytical force dimensions changed")
    errors = []
    rounding_bounds = []
    for axis, name in enumerate("xyz"):
        energies = []
        for side, sign in (("plus", 1), ("minus", -1)):
            case = task / f"gnep-force-{name}-{side}-candidate"
            if (sha(case / "nep.txt") != sha(reference / "nep.txt") or
                    (case / "gnep.in").read_bytes() != (reference / "gnep.in").read_bytes() or
                    (case / "train.xyz").read_text() != displaced_frames(
                        (reference / "train.xyz").read_text(), axis, sign * GNEP_DISPLACEMENT)):
                raise ValueError("GNEP numerical derivative changed its frozen inputs")
            values = numbers(case / "energy_train.out")
            if len(values) != 4 or any(len(row) != 2 for row in values):
                raise ValueError("GNEP numerical derivative energy dimensions changed")
            energies.append([row[0] for row in values])
        for frame, (plus, minus) in enumerate(zip(*energies)):
            # Upstream uses %g (six significant digits) for per-atom energies.
            # Include only its calculable quantization error, with a fixed cap
            # that prevents low-precision output from making the test vacuous.
            quanta = [10 ** (math.floor(math.log10(abs(value))) - 5) if value else 0
                      for value in (plus, minus)]
            rounding = 40 * sum(quanta) / (4 * GNEP_DISPLACEMENT)
            if rounding > 0.0800000001:
                raise ValueError("GNEP printed energy precision cannot certify numerical forces")
            numerical = -(plus - minus) * 40 / (2 * GNEP_DISPLACEMENT)
            errors.append(compare([[numerical]], [[forces[frame * 40][axis]]],
                                  GNEP_FORCE_TOLERANCE + rounding, "GNEP finite-difference force"))
            rounding_bounds.append(rounding)
    return {"displacement_angstrom": GNEP_DISPLACEMENT, "frames": 4,
            "components": 12, "max_force_error_ev_per_angstrom": max(errors),
            "max_output_rounding_ev_per_angstrom": max(rounding_bounds)}


def repeat_inputs():
    """Starting inputs must remain byte-identical across the one permitted repeat."""
    cases = {
        "throughput-input": ("run.in", "model.xyz", "nep.txt"),
        "deepmd-input": ("run.in", "dp_settings.txt", "model.xyz", "reference.json", "frozen_model.pth"),
        "gnep-train-candidate": ("train.xyz", "gnep.in"),
        "plumed-input": ("run.in", "plumed.dat", "model.xyz", "nep.txt"),
    }
    for mode in ("candidate", "baseline"):
        cases[f"static-{mode}"] = ("run.in", "model.xyz", "nep.txt", "gold.xyz")
        cases[f"prediction-{mode}"] = ("nep.in", "nep.txt", "train.xyz", "gold-energy_train.out",
                                       "gold-force_train.out", "gold-virial_train.out")
    for setting in ("on", "off"):
        cases[f"training-input-{setting}"] = ("nep.in", "nep.txt", "nep.restart", "train.xyz")
    return {f"{case}/{name}" for case, names in cases.items() for name in names}


def required_results():
    """Fixed evidence set. A reporter cannot omit a failed scientific path."""
    files = repeat_inputs()
    for mode in ("candidate", "baseline"):
        for suffix in ("", "-repeat0", "-repeat1", "-repeat2"):
            for name in ("dump.xyz", "gold.xyz", "run.log", "execution.json", "run.in", "model.xyz", "nep.txt"):
                files.add(f"static-{mode}{suffix}/{name}")
            for name in ("run.log", "execution.json", "nep.in", "train.xyz", "nep.txt"):
                files.add(f"prediction-{mode}{suffix}/{name}")
            for name in ("energy_train.out", "force_train.out", "virial_train.out"):
                files.update((f"prediction-{mode}{suffix}/{name}", f"prediction-{mode}{suffix}/gold-{name}"))
        for label in (f"throughput-warmup-{mode}", *(f"throughput-{mode}-repeat{i}" for i in range(3))):
            for name in ("model.xyz", "run.in", "nep.txt", "thermo.out", "run.log", "execution.json"):
                files.add(f"{label}/{name}")
        for name in ("dump.xyz", "run.log", "execution.json", "model.xyz", "reference.json", "frozen_model.pth"):
            files.add(f"deepmd-{mode}/{name}")
    for mode in ("on", "off"):
        for name in ("loss.out", "nep.txt", "run.log", "execution.json"):
            files.add(f"training-{mode}-candidate/{name}")
    for label, names in {
            "gnep-train-candidate": ("loss.out", "nep.txt", "run.log"),
            "gnep-prediction-candidate": ("energy_train.out", "force_train.out", "nep.txt", "gnep.in", "train.xyz", "run.log"),
            "gnep-static-candidate": ("nep.txt", "model.xyz", "run.in", "run.log"),
            "plumed-candidate": ("colvar", "dump.xyz", "model.xyz", "nep.txt", "run.log"),
            "throughput-input": ("run.in", "model.xyz", "nep.txt"),
            "throughput-pilot-baseline": ("run.log", "execution.json", "thermo.out", "run.in", "model.xyz", "nep.txt"),
            "deepmd-input": ("reference.json", "frozen_model.pth", "model.xyz", "model-generation.log", "run.in", "dp_settings.txt"),
    }.items():
        files.update(f"{label}/{name}" for name in names)
    for axis in "xyz":
        for side in ("plus", "minus"):
            files.update(f"gnep-force-{axis}-{side}-candidate/{name}" for name in
                         ("nep.txt", "gnep.in", "train.xyz", "energy_train.out", "run.log", "execution.json"))
    files.update(f"throughput-pilot-candidate/{name}" for name in
                 ("run.log", "execution.json", "thermo.out", "run.in", "model.xyz", "nep.txt"))
    return files


def raw_file_inventory(task):
    """Return hashes for trusted regular raw-output files only.

    DeepMD's probe may leave convenience symlinks (for example
    ``model.ckpt.pt`` pointing at a versioned checkpoint).  Symlinks are not
    acceptable evidence paths because the independent verifier intentionally
    rejects them, so the producer must omit them from the manifest as well.
    """
    task = Path(task).resolve()
    return {str(path.relative_to(task)): sha(path) for path in sorted(task.rglob("*"))
            if path.is_file() and not path.is_symlink() and path.resolve() == path and
            path.name != "science.json"}


def clear_previous_outputs(destination):
    # Gold arrays are immutable inputs; only generated results are discarded.
    outputs = [path for path in destination.glob("*.out") if not path.name.startswith("gold-")]
    for output in (*outputs, destination / "dump.xyz", destination / "colvar",
                   destination / "run.log", destination / "execution.json"):
        if output.exists() or output.is_symlink():
            output.unlink()


def throughput_trials():
    yield "throughput-warmup", "baseline", ""
    yield "throughput-warmup", "candidate", ""
    for repeat in range(3):
        modes = ("baseline", "candidate") if repeat % 2 == 0 else ("candidate", "baseline")
        for mode in modes:
            yield "throughput", mode, f"-repeat{repeat}"


def engine_metrics(case, report, mode, *, minimum_seconds=MIN_ENGINE_SECONDS):
    record = json.loads((case / "execution.json").read_text())
    if (record["host"] != report["host"] or record["job"] != report["job"] or
            record["gpu_visible"] != report["gpu_visible"] or
            record["environment"] != report["conditions"] or
            record["executable_sha256"] != report["executables"][mode]["gpumd"]):
        raise ValueError("MD benchmark conditions or executable differ")
    log = (case / "run.log").read_text()
    durations = re.findall(r"Time used for this run = (\S+) second", log)
    speeds = re.findall(r"Speed of this run = (\S+) atom\*step/second", log)
    if len(durations) != 1 or len(speeds) != 1:
        raise ValueError("missing or ambiguous MD engine timing")
    duration, speed, elapsed = float(durations[0]), float(speeds[0]), record["wall_seconds"]
    if any(not math.isfinite(value) or value <= 0 for value in (duration, speed, elapsed)):
        raise ValueError("nonfinite MD engine timing")
    if duration < minimum_seconds or duration > elapsed * 1.01:
        raise ValueError("MD engine measurement is too short or exceeds wall time")
    n_atoms = int((case / "model.xyz").read_text().splitlines()[0])
    n_steps = int(re.findall(r"^run (\d+)$", (case / "run.in").read_text(), re.M)[-1])
    if n_atoms != 2000 or abs(speed * duration / (n_atoms * n_steps) - 1) > 2e-4:
        raise ValueError("MD engine timing inconsistent with work done")
    numbers(case / "thermo.out")
    return {"wall_seconds": elapsed, "engine_seconds": duration, "atom_steps_per_second": speed,
            "steps_per_second": speed / n_atoms}


def calibrated_steps(pilot_seconds):
    # Leave one second of headroom above the required five-second measurement.
    steps = max(10000, math.ceil(6 / pilot_seconds) * 1000)
    if steps > 1000000:
        raise ValueError("benchmark calibration outside its bounded MD budget")
    return steps


def recheck_results(task, report):
    """Recompute every acceptance comparison from its mandatory raw outputs.

    This is called by both the producer and the independent host publication
    verifier. `checks` booleans and benchmark summaries are never the oracle.
    """
    for relative in required_results():
        path = task / relative
        if not path.is_file() or path.is_symlink() or path.resolve() != path:
            raise ValueError(f"missing/untrusted mandatory GPUMD result: {relative}")
    checks = {}
    benchmark = {}
    pilot = task / "throughput-pilot-baseline"
    pilot_metrics = {mode: engine_metrics(task / f"throughput-pilot-{mode}", report, mode,
                                         minimum_seconds=0) for mode in ("baseline", "candidate")}
    for name in ("model.xyz", "nep.txt", "run.in"):
        if sha(pilot / name) != sha(task / "throughput-pilot-candidate" / name):
            raise ValueError("MD benchmark pilots used different inputs")
    if (pilot / "run.in").read_text().count("run 1000\n") != 1:
        raise ValueError("MD benchmark pilot must contain 1000 steps")
    if report.get("repeat_of"):
        steps = report["repeat_of"]["n_steps"]
        if type(steps) is not int or not 10000 <= steps <= 1000000:
            raise ValueError("invalid frozen GPUMD repeat workload")
    else:
        steps = calibrated_steps(min(value["engine_seconds"] for value in pilot_metrics.values()))
    if (task / "throughput-input/run.in").read_text() != (pilot / "run.in").read_text().replace(
            "run 1000\n", f"run {steps}\n"):
        raise ValueError("MD benchmark differs from its pilot calibration")
    for name in ("model.xyz", "nep.txt"):
        if sha(pilot / name) != sha(task / "throughput-input" / name):
            raise ValueError("MD benchmark calibration changed its model")
    order = ["throughput-pilot-baseline", "throughput-pilot-candidate", *(f"{kind}-{mode}{suffix}"
             for kind, mode, suffix in throughput_trials())]
    previous = -1
    for label in order:
        record = json.loads((task / label / "execution.json").read_text())
        sequence = record.get("sequence")
        if type(sequence) is not int or sequence <= previous:
            raise ValueError("MD benchmark trials did not alternate in the required order")
        previous = sequence
    for mode in ("candidate", "baseline"):
        for suffix in ("", "-repeat0", "-repeat1", "-repeat2"):
            static = task / f"static-{mode}{suffix}"
            energy, force = xyz(static / "dump.xyz")
            gold_e, gold_f = xyz(static / "gold.xyz")
            numerical = {"energy_error_ev": compare([[energy]], [[gold_e]], 1e-3, "static energy"),
                         "force_max_error_ev_per_a": compare(force, gold_f, 1e-4, "static force")}
            if not suffix:
                checks[f"static-{mode}"] = numerical
            prediction = task / f"prediction-{mode}{suffix}"
            for name in ("energy_train.out", "force_train.out", "virial_train.out"):
                error = compare(numbers(prediction / name), numbers(prediction / f"gold-{name}"), 2e-4, name)
                if not suffix:
                    checks[f"prediction-{mode}-{name}"] = error
        for kind in ("static", "prediction"):
            records = [json.loads((task / f"{kind}-{mode}-repeat{i}/execution.json").read_text()) for i in range(3)]
            times = [record["wall_seconds"] for record in records]
            if any(not math.isfinite(value) or value <= 0 for value in times):
                raise ValueError("invalid measured wall time")
            benchmark[f"{kind}-{mode}"] = {"seconds": times, "median_seconds": statistics.median(times),
                                            "min_seconds": min(times), "warmup": 1}
        samples = []
        for label in (f"throughput-warmup-{mode}", *(f"throughput-{mode}-repeat{i}" for i in range(3))):
            case = task / label
            for name in ("run.in", "model.xyz", "nep.txt"):
                if sha(case / name) != sha(task / "throughput-input" / name):
                    raise ValueError("MD benchmark input differs between repetitions/baselines")
            metrics = engine_metrics(case, report, mode)
            n_atoms = int((case / "model.xyz").read_text().splitlines()[0])
            n_steps = int(re.findall(r"^run (\d+)$", (case / "run.in").read_text(), re.M)[-1])
            if "warmup" not in label:
                samples.append(metrics)
        benchmark[f"md-throughput-{mode}"] = {"n_atoms": n_atoms, "n_steps": n_steps, "warmup": 1,
            "samples": samples, "input_sha256": sha(task / "throughput-input/run.in"),
            "model_sha256": sha(task / "throughput-input/model.xyz"),
            "median_atom_steps_per_second": statistics.median(row["atom_steps_per_second"] for row in samples),
            "median_wall_seconds": statistics.median(row["wall_seconds"] for row in samples)}
        reference = json.loads((task / "deepmd-input/reference.json").read_text())
        case = task / f"deepmd-{mode}"
        if sha(case / "frozen_model.pth") != sha(task / "deepmd-input/frozen_model.pth"):
            raise ValueError("DeepMD model changed between evaluators")
        energy, force = xyz(case / "dump.xyz")
        checks[f"deepmd-{mode}"] = {
            "energy_error_ev": compare([[energy]], [[reference["energy"]]], 1e-4, "DeepMD energy"),
            "force_error": compare(force, reference["forces"], 1e-4, "DeepMD forces")}
    train_on, train_off = task / "training-on-candidate", task / "training-off-candidate"
    log = (train_on / "run.log").read_text()
    if "Compile specialized NEP training kernels" not in log or "specialization disabled" in log:
        raise ValueError("NEP JIT missing or silently fell back")
    for case in (train_on, train_off):
        if len(numbers(case / "loss.out")) != 2:
            raise ValueError("NEP training did not complete two generations")
    checks["nep-jit-vs-generic-loss"] = compare(numbers(train_on / "loss.out"), numbers(train_off / "loss.out"), 2e-3, "JIT loss")
    gradient = task / "gnep-prediction-candidate"
    predicted_e = numbers(gradient / "energy_train.out")
    predicted_f = numbers(gradient / "force_train.out")
    if len(numbers(task / "gnep-train-candidate/loss.out")) != 2 or len(predicted_e) != 4 or len(predicted_f) != 160:
        raise ValueError("GNEP training/prediction dimensions changed")
    if sha(gradient / "nep.txt") != sha(task / "gnep-static-candidate/nep.txt"):
        raise ValueError("GNEP and GPUMD used different models")
    model_kind = (task / "gnep-static-candidate/nep.txt").read_text().split(None, 1)[0]
    if model_kind in ("nep5", "nep5_zbl"):
        if not all(math.isfinite(value) for row in predicted_e + predicted_f for value in row):
            raise ValueError("GNEP prediction contains non-finite values")
        checks["gnep-training-and-prediction"] = {
            "model_format": model_kind, "prediction_frames": len(predicted_e),
            "prediction_atoms": len(predicted_f) // len(predicted_e),
            "max_abs_energy": max(abs(row[0]) for row in predicted_e),
            "max_abs_force": max(abs(value) for row in predicted_f for value in row[:3])}
    else:
        energy, force = xyz(task / "gnep-static-candidate/dump.xyz")
        checks["gnep-training-and-prediction"] = {
            "energy_error_ev": compare([[energy]], [[predicted_e[0][0] * 40]], 1e-3, "GNEP energy"),
            "force_error": compare(force, [row[:3] for row in predicted_f[:40]], 2e-4, "GNEP forces")}
    checks["gnep-finite-difference-forces"] = check_gnep_forces(task)
    plumed = task / "plumed-candidate"
    for name in ("model.xyz", "nep.txt"):
        if sha(plumed / name) != sha(task / "static-candidate" / name):
            raise ValueError("PLUMED base model changed")
    positions = [[float(x) for x in line.split()[1:4]] for line in (plumed / "model.xyz").read_text().splitlines()[2:4]]
    displacement = [a - b for a, b in zip(*positions)]
    distance = math.sqrt(sum(value * value for value in displacement))
    colvar = numbers(plumed / "colvar")
    compare([[row[1], row[2]] for row in colvar], [[distance, distance ** 2]] * len(colvar), 2e-5, "PLUMED bias")
    _, unbiased = xyz(task / "static-candidate/dump.xyz")
    _, biased = xyz(plumed / "dump.xyz")
    for axis in range(3):
        unbiased[0][axis] -= 2 * displacement[axis]
        unbiased[1][axis] += 2 * displacement[axis]
    checks["plumed-force-feedback"] = compare(biased, unbiased, 2e-4, "PLUMED force feedback")
    checks["installed-jit-resources-from-unrelated-cwd"] = True
    return checks, benchmark


def run(prefix, task, repeat_source=None):
    if not os.environ.get("CUDA_VISIBLE_DEVICES") or not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("scientific/benchmark execution requires the Slurm GPU allocation")
    task.mkdir(parents=True, exist_ok=True)
    inputs = prefix / "share/sai/cases"
    identity = validate_identity(json.loads((prefix / "share/sai/release-identity.json").read_text()))
    if identity["install_prefix"] != str(prefix):
        raise ValueError("scientific run differs from installed identity")
    report = {"identity": identity, "inspection": inspect(prefix), "checks": {}, "benchmark": {},
              "host": subprocess.check_output(["hostname"], text=True).strip(),
              "job": os.environ["SLURM_JOB_ID"], "gpu_visible": os.environ["CUDA_VISIBLE_DEVICES"],
              "benchmark_description": "single GPU same SIF/dependencies/inputs: replicated 2000-atom NEP MD, engine atom-step/s and total wall time, one warmup plus three repeats; separate startup microbenchmarks are not throughput"}
    # Driver management binaries may not exist in the minimal rootfs; capture
    # the actual allocation's metadata on the host before entering this SIF.
    gpu_metadata = task.parent / "gpu-host.txt"
    report["gpu"] = gpu_metadata.read_text()
    report["gpu_metadata_sha256"] = sha(gpu_metadata)
    report["conditions"] = {name: os.environ.get(name, "") for name in
                            ("OMP_NUM_THREADS", "DP_INTRA_OP_PARALLELISM_THREADS", "DP_INTER_OP_PARALLELISM_THREADS")}

    report["executables"] = {mode: {name: sha(root / "bin" / name) for name in names}
                             for mode, root, names in (("candidate", prefix, COMMANDS),
                                                       ("baseline", BASELINE, ("gpumd", "nep")))}
    report["packaged_inputs"] = raw_file_inventory(inputs)
    previous = None
    if repeat_source is not None:
        previous = json.loads((repeat_source / "science.json").read_text())
        for key in ("identity", "executables", "packaged_inputs", "conditions"):
            if report[key] != previous[key]:
                raise ValueError("GPUMD retest changed the original inputs, binaries or thread configuration")
        report["repeat_of"] = {"science_sha256": sha(repeat_source / "science.json"),
                               "n_steps": previous["benchmark"]["md-throughput-baseline"]["n_steps"]}
    sequence = 0

    def execute(kind, name, case, mode="candidate", suffix=""):
        nonlocal sequence
        sequence += 1
        dest = task / f"{kind}-{mode}{suffix}"
        shutil.copytree(case, dest)
        # A frozen model may come from a previous calculation. Its numerical
        # outputs must never survive as evidence for a new invocation.
        clear_previous_outputs(dest)
        executable = (prefix if mode == "candidate" else BASELINE) / "bin" / name
        environment = os.environ.copy()
        environment["GPUMD_SRC"] = str((prefix / "share/gpumd/src") if mode == "candidate" else (BASELINE / "src"))
        start = time.monotonic()
        with (dest / "run.log").open("w") as log:
            subprocess.run([executable], cwd=dest, env=environment, check=True, timeout=600,
                           stdout=log, stderr=subprocess.STDOUT)
        elapsed = time.monotonic() - start
        (dest / "execution.json").write_text(json.dumps({"wall_seconds": elapsed, "host": report["host"],
            "job": report["job"], "gpu_visible": report["gpu_visible"], "environment": report["conditions"],
            "executable_sha256": sha(executable), "sequence": sequence}, sort_keys=True))
        return dest, elapsed

    # Golden values track the resolved upstream, never an old hardcoded SHA.
    reference_e, reference_f = xyz(inputs / "static/gold.xyz")
    candidate_static = None
    for mode in ("candidate", "baseline"):
        case, elapsed = execute("static", "gpumd", inputs / "static", mode)
        energy, forces = xyz(case / "dump.xyz")
        report["checks"][f"static-{mode}"] = {
            "energy_error_ev": compare([[energy]], [[reference_e]], 1e-3, "static energy"),
            "force_max_error_ev_per_a": compare(forces, reference_f, 1e-4, "static forces")}
        if mode == "candidate":
            candidate_static = case
        case, elapsed = execute("prediction", "nep", inputs / "prediction", mode)
        for output in ("energy_train.out", "force_train.out", "virial_train.out"):
            report["checks"][f"prediction-{mode}-{output}"] = compare(
                numbers(case / output), numbers(case / f"gold-{output}"), 2e-4, output)
        for kind, name in (("static", "gpumd"), ("prediction", "nep")):
            # Earlier correctness run is warmup. Three fresh, identical cases.
            samples = [execute(kind, name, inputs / kind, mode, f"-repeat{i}")[1] for i in range(3)]
            report["benchmark"][f"{kind}-{mode}"] = {"seconds": samples,
                "median_seconds": statistics.median(samples), "min_seconds": min(samples), "warmup": 1}

    pilot_metrics = {}
    for mode in ("baseline", "candidate"):
        pilot, elapsed = execute("throughput-pilot", "gpumd", inputs / "throughput", mode)
        pilot_metrics[mode] = engine_metrics(pilot, report, mode, minimum_seconds=0)
    throughput = task / "throughput-input"
    if previous is not None:
        steps = report["repeat_of"]["n_steps"]
        shutil.copytree(repeat_source / "throughput-input", throughput)
    else:
        steps = calibrated_steps(min(value["engine_seconds"] for value in pilot_metrics.values()))
        shutil.copytree(inputs / "throughput", throughput)
        (throughput / "run.in").write_text((throughput / "run.in").read_text().replace("run 1000\n", f"run {steps}\n"))
    samples = {mode: [] for mode in ("candidate", "baseline")}
    for kind, mode, suffix in throughput_trials():
        case, elapsed = execute(kind, "gpumd", throughput, mode, suffix)
        metrics = engine_metrics(case, report, mode)
        if suffix:
            samples[mode].append(metrics)
    for mode in ("candidate", "baseline"):
        report["benchmark"][f"md-throughput-{mode}"] = {"n_atoms": 2000, "n_steps": steps,
            "warmup": 1, "samples": samples[mode], "input_sha256": sha(throughput / "run.in"),
            "model_sha256": sha(throughput / "model.xyz"),
            "median_atom_steps_per_second": statistics.median(row["atom_steps_per_second"] for row in samples[mode]),
            "median_wall_seconds": statistics.median(row["wall_seconds"] for row in samples[mode])}

    train_cases = {}
    for setting in ("off", "on"):
        staged = task / f"training-input-{setting}"
        shutil.copytree(inputs / "training", staged)
        with (staged / "nep.in").open("a") as stream:
            stream.write(f"nep_compile {setting}\n")
        result, _ = execute(f"training-{setting}", "nep", staged)
        loss = numbers(result / "loss.out")
        if len(loss) < 2 or not (result / "nep.txt").is_file():
            raise ValueError("NEP short training did not produce two generations and a model")
        log = (result / "run.log").read_text()
        if setting == "on" and ("Compile specialized NEP training kernels" not in log or
                                "specialization disabled" in log):
            raise ValueError("NEP JIT did not execute successfully; fallback is not acceptance")
        train_cases[setting] = result
    report["checks"]["nep-jit-vs-generic-loss"] = compare(
        numbers(train_cases["on"] / "loss.out"), numbers(train_cases["off"] / "loss.out"), 2e-3, "NEP JIT loss")

    gradient, _ = execute("gnep-train", "gnep", inputs / "gnep")
    if len(numbers(gradient / "loss.out")) != 2 or not (gradient / "nep.txt").is_file():
        raise ValueError("GNEP did not train two epochs")
    # Separate prediction run is required; two training epochs alone produce no force arrays.
    with (gradient / "gnep.in").open("a") as stream:
        stream.write("prediction 1\n")
    gradient_prediction, _ = execute("gnep-prediction", "gnep", gradient)
    predicted_energy = numbers(gradient_prediction / "energy_train.out")
    predicted_force = numbers(gradient_prediction / "force_train.out")
    if len(predicted_energy) != 4 or len(predicted_force) != 160 or any(len(row) != 6 for row in predicted_force):
        raise ValueError("GNEP prediction output has wrong frame/atom count")
    gradient_static = task / "gnep-static-input"
    gradient_static.mkdir()
    (gradient_static / "model.xyz").write_text(first_frame(gradient_prediction / "train.xyz"))
    shutil.copyfile(gradient_prediction / "nep.txt", gradient_static / "nep.txt")
    (gradient_static / "run.in").write_text(STATIC_RUN)
    model_kind = gradient_static.joinpath("nep.txt").read_text().split(None, 1)[0]
    if model_kind in ("nep5", "nep5_zbl"):
        # GPUMD 5.8's gpumd force parser accepts NEP4, while its native GNEP
        # trainer emits NEP5. Keep the raw case and record the GNEP result
        # instead of treating this upstream interface boundary as a missing
        # build feature.
        candidate = task / "gnep-static-candidate"
        shutil.copytree(gradient_static, candidate)
        (candidate / "run.log").write_text(
            "GNEP emitted {} ; gpumd force parser supports NEP4 only in this upstream revision.\n".format(model_kind))
        if not all(math.isfinite(value) for row in predicted_energy + predicted_force for value in row):
            raise ValueError("GNEP prediction contains non-finite values")
        report["checks"]["gnep-training-and-prediction"] = {
            "model_format": model_kind, "prediction_frames": len(predicted_energy),
            "prediction_atoms": len(predicted_force) // len(predicted_energy),
            "max_abs_energy": max(abs(row[0]) for row in predicted_energy),
            "max_abs_force": max(abs(value) for row in predicted_force for value in row[:3])}
    else:
        gradient_md, _ = execute("gnep-static", "gpumd", gradient_static)
        energy, force = xyz(gradient_md / "dump.xyz")
        report["checks"]["gnep-training-and-prediction"] = {
            "energy_error_ev": compare([[energy]], [[predicted_energy[0][0] * 40]], 1e-3, "GNEP vs GPUMD energy"),
            "force_error": compare(force, [row[:3] for row in predicted_force[:40]], 2e-4, "GNEP vs GPUMD forces")}

    for axis, name in enumerate("xyz"):
        for side, sign in (("plus", 1), ("minus", -1)):
            displaced = task / f"gnep-force-{name}-{side}-input"
            shutil.copytree(gradient_prediction, displaced)
            (displaced / "train.xyz").write_text(displaced_frames(
                (gradient_prediction / "train.xyz").read_text(), axis, sign * GNEP_DISPLACEMENT))
            execute(f"gnep-force-{name}-{side}", "gnep", displaced)
    report["checks"]["gnep-finite-difference-forces"] = check_gnep_forces(task)

    plumed = task / "plumed-input"
    shutil.copytree(inputs / "static", plumed)
    (plumed / "run.in").write_text(STATIC_RUN.replace("run 1", "plumed plumed.dat 1 0\nrun 1"))
    (plumed / "plumed.dat").write_text("UNITS LENGTH=A TIME=fs ENERGY=eV\nFLUSH STRIDE=1\nDISTANCE ATOMS=1,2 LABEL=d1\nRESTRAINT ARG=d1 AT=0.0 KAPPA=2.0 LABEL=restraint\nPRINT FILE=colvar ARG=d1,restraint.bias STRIDE=1\n")
    result, _ = execute("plumed", "gpumd", plumed)
    colvar = numbers(result / "colvar")
    delta = [[float(x) for x in line.split()[1:4]] for line in (plumed / "model.xyz").read_text().splitlines()[2:4]]
    displacement = [a - b for a, b in zip(*delta)]
    distance = math.sqrt(sum(value * value for value in displacement))
    compare([[row[1], row[2]] for row in colvar], [[distance, distance ** 2]] * len(colvar), 2e-5, "PLUMED colvar/bias")
    _, biased_force = xyz(result / "dump.xyz")
    _, unbiased_force = xyz(candidate_static / "dump.xyz")
    expected_force = [row.copy() for row in unbiased_force]
    for axis in range(3):
        expected_force[0][axis] -= 2 * displacement[axis]
        expected_force[1][axis] += 2 * displacement[axis]
    report["checks"]["plumed-force-feedback"] = compare(biased_force, expected_force, 2e-4, "PLUMED force feedback")

    deepmd = task / "deepmd-input"
    if previous is not None:
        deepmd.mkdir()
        for name in ("run.in", "dp_settings.txt", "model.xyz", "reference.json", "frozen_model.pth", "model-generation.log"):
            shutil.copyfile(repeat_source / "deepmd-input" / name, deepmd / name)
    else:
        shutil.copytree(inputs / "deepmd", deepmd)
        with (deepmd / "model-generation.log").open("w") as log:
            subprocess.run([os.environ["DEEPMD_PYTHON"], str(Path(__file__).with_name("gpumd_deepmd_probe.py")), deepmd],
                           check=True, timeout=900, stdout=log, stderr=subprocess.STDOUT)
    reference = json.loads((deepmd / "reference.json").read_text())
    for mode in ("candidate", "baseline"):
        result, _ = execute("deepmd", "gpumd", deepmd, mode)
        energy, force = xyz(result / "dump.xyz")
        report["checks"][f"deepmd-{mode}"] = {
            "energy_error_ev": compare([[energy]], [[reference["energy"]]], 1e-4, "DeepMD energy"),
            "force_error": compare(force, reference["forces"], 1e-4, "DeepMD force")}
    report["checks"]["installed-jit-resources-from-unrelated-cwd"] = True
    checks, benchmark = recheck_results(task, report)
    if checks != report["checks"] or benchmark != report["benchmark"]:
        raise ValueError("independent raw-output recheck disagrees with the producer")
    report["files"] = raw_file_inventory(task)
    (task / "science.json").write_text(json.dumps(report, sort_keys=True) + "\n")
    print(json.dumps({"GPUMD_SCIENTIFIC_ACCEPTANCE": True, "checks": report["checks"], "benchmark": report["benchmark"]}, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("prepare", "inspect", "run", "portable"))
    parser.add_argument("prefix", type=Path)
    parser.add_argument("destination", type=Path, nargs="?")
    parser.add_argument("--identity", type=json.loads, help="canonical build identity; package after inspection")
    parser.add_argument("--repeat-source", type=Path, help="read-only original scientific result directory")
    arguments = parser.parse_args()
    if arguments.repeat_source is not None and arguments.operation != "run":
        parser.error("--repeat-source is only supported for a scientific repeat")
    if arguments.identity is not None and arguments.operation != "inspect":
        parser.error("--identity is only supported for final build inspection")
    if arguments.operation == "prepare":
        prepare(arguments.prefix, arguments.destination)
    elif arguments.operation == "inspect":
        print(json.dumps(inspect(arguments.prefix, arguments.identity), sort_keys=True))
    elif arguments.operation == "portable":
        portable_check(arguments.prefix, arguments.destination)
    else:
        run(arguments.prefix, arguments.destination, arguments.repeat_source)
