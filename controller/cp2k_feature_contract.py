#!/usr/bin/env python3
"""Fail-closed CP2K artifact checks; scientific acceptance is a separate gate."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

BASE_FLAGS = frozenset("omp libint fftw3 libxc elpa parallel scalapack mpi_f08 cosma plumed2 spglib libdftd4 mctc-lib tblite libvori libbqb hdf5".split())
GPU_FLAGS = frozenset("dbcsr_acc offload_cuda cusolvermp cusolvermp_nccl".split())
REQUIRED_OPTIONS = tuple("MPI MPI_F08 FFTW3 LIBXC LIBINT2 ELPA COSMA LIBXS LIBXSMM PLUMED SPGLIB VORI HDF5 DFTD4 TBLITE".split())
TRANSIENT = re.compile(r"/(?:workspace|control|input)(?:/|$)")


def version_flags(text):
    rows = re.findall(r"^\s*cp2kflags:\s*(.+)$", text, re.MULTILINE)
    if len(rows) != 1:
        raise ValueError("missing or ambiguous CP2K flags")
    return set(rows[0].split())


def check_flags(text, target):
    flags = version_flags(text)
    missing = set((BASE_FLAGS | (GPU_FLAGS if target != "dsprhbm" else set())) - flags)
    # CP2K 2026.2 split the old xsmm flag into LIBXS and LIBXSMM.
    if not {"libxs", "libxsmm"}.issubset(flags):
        missing.add("libxs+libxsmm")
    if missing:
        raise ValueError("CP2K feature loss: " + ", ".join(sorted(missing)))
    if target == "dsprhbm" and flags & GPU_FLAGS:
        raise ValueError("CPU artifact unexpectedly requires GPU offload")
    return sorted(flags)


def check_cache(text, prefix, target):
    # Parse one physical line at a time. Negated character classes containing
    # newlines otherwise swallow blank lines and // comments into option names.
    cache = {}
    for line in text.splitlines():
        if not line or line.startswith(("#", "//")):
            continue
        entry = re.fullmatch(r"([^:=]+):([^=]+)=(.*)", line)
        if entry:
            key, _, value = entry.groups()
            if key in cache:
                raise ValueError("duplicate CMake cache option: " + key)
            cache[key] = value
    for option in REQUIRED_OPTIONS:
        if cache.get("CP2K_USE_" + option) not in ("ON", "TRUE", "1"):
            raise ValueError("required CP2K option is disabled: " + option)
    if cache.get("CMAKE_INSTALL_PREFIX") != str(prefix):
        raise ValueError("CP2K installation prefix changed")
    for language in ("C", "CXX", "Fortran"):
        if "-march=native" not in cache.get(f"CMAKE_{language}_FLAGS", "").split():
            raise ValueError("CP2K must be compiled natively on each target")
    if cache.get("CP2K_USE_ACCEL") != ("NONE" if target == "dsprhbm" else "CUDA"):
        raise ValueError("wrong CP2K accelerator backend")
    elpa = cache.get("CP2K_ELPA_ROOT", "")
    if not elpa.startswith("/opt/devtools/elpa/elpa-2026.02.001-2603-gnu/"):
        raise ValueError("ELPA must use the pinned 2026 system module")
    if target != "dsprhbm" and cache.get("CP2K_USE_CUSOLVER_MP") != "ON":
        raise ValueError("cuSOLVERMp was not enabled")
    return cache


def check_linkage(text, target):
    if "not found" in text or TRANSIENT.search(text):
        raise ValueError("unresolved or transient CP2K dynamic dependency")
    if "elpa-2024" in text:
        raise ValueError("CP2K linked the obsolete toolchain ELPA")
    if "libelpa" not in text or "/opt/devtools/elpa/elpa-2026.02.001-2603-gnu/" not in text:
        raise ValueError("ELPA 2026 dynamic linkage was not proven")
    if target != "dsprhbm":
        for library, root in (("libcusolverMp.so", "/opt/devtools/nvidia/mp_libs/lib/"),
                              ("libnccl.so", "/opt/devtools/nvidia/nccl_2.29.3_")):
            if not any(library in line and root in line for line in text.splitlines()):
                raise ValueError("wrong or missing runtime library: " + library)


def check_dynamic_paths(text):
    for line in text.splitlines():
        if "RPATH" in line or "RUNPATH" in line:
            if TRANSIENT.search(line):
                raise ValueError("installed ELF retains build-only RPATH")


def inside(path, root):
    """Path-component comparison (works on the cluster's Python 3.8 too)."""
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def runtime_roots(target):
    isa = "avx2" if target in ("16v100-avx2", "8v100v0-avx512") else "avx512"
    # Only the CP2K site dependency tree is approved, never all of /opt/apps.
    return ("/opt/devtools", "/usr", "/bin", "/lib", "/lib64",
            f"/opt/apps/cp2k/cp2k-2026.1-{isa}/tools/toolchain/install")


def check_dynamic(text, binary, prefix, target):
    """Validate loader tags for every packaged ELF, including dependencies."""
    binary, prefix = Path(binary), Path(prefix)
    tags = []
    for tag, value in re.findall(r"\((NEEDED|RPATH|RUNPATH)\).*?\[(.*?)\]", text):
        tags.append({"tag": tag, "value": value})
        if tag == "NEEDED":
            if not value or "/" in value:
                raise ValueError(f"path-valued DT_NEEDED in {binary}: {value}")
            continue
        for entry in value.split(":"):
            expanded = entry.replace("${ORIGIN}", str(binary.parent)).replace("$ORIGIN", str(binary.parent))
            path = Path(expanded)
            if (not entry or "$" in expanded or not path.is_absolute() or
                    not any(inside(path, root) for root in (prefix, *runtime_roots(target)))):
                raise ValueError(f"untrusted {tag} in {binary}: {entry}")
    for interpreter in re.findall(r"Requesting program interpreter:\s*([^\]]+)\]", text):
        if not Path(interpreter).is_absolute() or not any(inside(interpreter, root) for root in
                                                        ("/usr/lib", "/usr/lib64", "/lib", "/lib64")):
            raise ValueError(f"untrusted ELF interpreter in {binary}: {interpreter}")
        tags.append({"tag": "INTERP", "value": interpreter})
    return tags


def verify_tree(prefix, target):
    prefix = Path(prefix).resolve()
    result = {}
    for path in sorted(prefix.rglob("*")):
        if path.is_symlink():
            if not path.exists() or not inside(path, prefix):
                raise ValueError(f"broken or escaping installation symlink: {path}")
            continue
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                continue
        dynamic = run(["readelf", "-d", str(path)]) + run(["readelf", "-l", str(path)])
        result[str(path.relative_to(prefix))] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "dynamic": check_dynamic(dynamic, path, prefix, target)}
    if not result:
        raise ValueError("installation contains no verified ELF objects")
    return result


def check_runtime_environment(prefix, target):
    expected_data = str(Path(prefix) / "share/cp2k/data")
    if os.environ.get("CP2K_DATA_DIR") != expected_data:
        raise ValueError("CP2K_DATA_DIR must resolve to installed /opt data")
    for name in ("PATH", "LD_LIBRARY_PATH"):
        for entry in os.environ.get(name, "").split(":"):
            # Apptainer --nv adds its own host-driver directory.
            if name == "LD_LIBRARY_PATH" and entry == "/.singularity.d/libs":
                continue
            if (not entry or not Path(entry).is_absolute() or
                    not any(inside(entry, root) for root in (prefix, *runtime_roots(target)))):
                raise ValueError(f"untrusted installed {name}: {entry}")


def run(argv):
    return subprocess.run(argv, check=True, text=True, capture_output=True).stdout


def source_changes(prefix, sha):
    """Prove renamed/builtin features from this source, never silently waive them."""
    source = Path("/workspace/source")
    files = {name: (source / name).read_bytes() for name in
             ("src/CMakeLists.txt", "src/libgrpp_integrals.F", "CMakeLists.txt")}
    cmake = files["src/CMakeLists.txt"].decode()
    if "grpp/libgrpp.F" not in cmake or "grpp/grpp.c" not in cmake:
        raise ValueError("libgrpp is no longer proven built in")
    if "USE libgrpp" not in files["src/libgrpp_integrals.F"].decode():
        raise ValueError("libgrpp integration is absent")
    if "CP2K_USE_QUIP" in files["CMakeLists.txt"].decode():
        raise ValueError("QUIP support returned upstream; enable and test it")
    changes = {"source_sha": sha, "libgrpp": "builtin", "quip": "upstream_removed",
               "approved_parity_exceptions": {"quip": "user-approved CP2K 2026 upstream removal"},
               "evidence": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    (Path(prefix) / "share/sai/upstream-feature-changes.json").write_text(json.dumps(changes, sort_keys=True) + "\n")


def verify(prefix, target, sha):
    prefix = Path(prefix)
    metadata = prefix / "share/sai"
    if (metadata / "source-sha").read_text().strip() != sha or (metadata / "target").read_text().strip() != target:
        raise ValueError("artifact provenance mismatch")
    check_cache((metadata / "CMakeCache.txt").read_text(), prefix, target)
    env = (metadata / "runtime-env.sh").read_text()
    if TRANSIENT.search(env):
        raise ValueError("saved environment depends on build-only paths")
    for name in ("BASIS_MOLOPT", "GTH_POTENTIALS"):
        if not (prefix / "share/cp2k/data" / name).is_file():
            raise ValueError("installed CP2K scientific data missing: " + name)
    check_runtime_environment(prefix, target)
    version = run([str(prefix / "bin/cp2k.psmp"), "--version"])
    flags = check_flags(version, target)
    linkage = run(["ldd", str(prefix / "bin/cp2k.psmp")])
    check_linkage(linkage, target)
    elf = verify_tree(prefix, target)
    print(version, end="")
    print(linkage, end="")
    print(json.dumps({"artifact_checks": True, "scientific_acceptance": False,
                      "prefix": str(prefix), "target": target, "flags": flags,
                      "elf": elf, "external_runtime_roots": runtime_roots(target),
                      "deployment_scope": "loader contract checked; no claim of physical deployment"}, sort_keys=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("op", choices=("cache", "verify", "source-changes"))
    parser.add_argument("prefix", type=Path)
    parser.add_argument("target")
    parser.add_argument("sha")
    args = parser.parse_args()
    if args.op == "cache":
        check_cache(Path("/workspace/build/CMakeCache.txt").read_text(), args.prefix, args.target)
    elif args.op == "source-changes":
        source_changes(args.prefix, args.sha)
    else:
        verify(args.prefix, args.target, args.sha)


if __name__ == "__main__":
    main()
