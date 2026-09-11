#!/usr/bin/env python3
"""Pinned CP2K scientific comparison in one two-node allocation.

The small cases check correctness; a fixed periodic 64-water GPW workload
measures compute time separately. Warm-up is excluded from timing summaries. A speed ratio is never
computed before all energies, forces, features and rank placements pass.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import statistics
import subprocess
import time

from remote_controller import TARGETS, safe_name
from source_cache import checksum

ROOT = Path(os.environ.get("SAI_SOFTWARE_ROOT", Path.home() / "sai-hpc-software")).resolve()
CONTROL = Path(__file__).resolve().parent
PROOF = "cp2k_benchmark"
FIXTURE_REVISION = "6d276e9c480f2b55f5a0b9166e22d20d9a3f8da0"
DATA_HASHES = {
    "BASIS_MOLOPT": "686dd72e7601ee41d4a173e4c7c3946f1e750f03095090f44d3821c180a81a61",
    "GTH_POTENTIALS": "5a9bfbc3b37a55917ade4fb704b80546d210d843759b94e3915e74acfc3fafb2",
}
ENERGY_TOLERANCE_HA = 1e-6
FORCE_TOLERANCE_HA_BOHR = 1e-5
TBLITE_REFERENCE_HA = -7.1737299848
TBLITE_REFERENCE_TOLERANCE_HA = 1e-8
GPU_BASELINE = "cp2k/2026.1-cuda12.9-sm70-auto"
CPU_BASELINE = "cp2k/2025.1-cpu-auto"
APPROVED_EXCEPTIONS = {"quip": "user-approved CP2K 2026 upstream removal"}
BENCHMARK_TARGETS = {name for name in TARGETS if name == "dsprhbm" or "v100" in name}

# Versioned benchmark fixtures, deliberately independent of the moving build
# source. Coordinates: tests/xTB/regtest-1/h2o_dimer.inp at FIXTURE_REVISION.
# GPW/DFTD4 sections derive from tests/QS/regtest-dft-vdw-corr-4/
# pbe_dftd4_force.inp. Water energies are compared to actual site runs, not to
# an invented upstream reference. Both executables read identical data bytes.
WATER_COORDINATES = """      UNIT BOHR
      O -3.04199500181513 -0.24214003254736 0.00000554304386
      O 2.42678178394041 0.25434643589818 0.00000193078253
      H -1.24278357467135 0.08031063038246 -0.00000630765923
      H -3.83888180438040 1.38422361491940 -0.00000174113844
      H 2.84843789645690 -0.73836886176897 -1.45538523208327
      H 2.84844070046958 -0.73837178688373 1.45538580705456
"""
FORCE_PRINT = """  &PRINT
    &FORCES ON
      FILENAME =forces.xyz
      NDIGITS 12
    &END FORCES
  &END PRINT
"""
DFTD4_SECTION = """      &VDW_POTENTIAL
        DISPERSION_FUNCTIONAL PAIR_POTENTIAL
        &PAIR_POTENTIAL
          REFERENCE_FUNCTIONAL PBE
          TYPE DFTD4
          &PRINT_DFTD
          &END PRINT_DFTD
        &END PAIR_POTENTIAL
      &END VDW_POTENTIAL
"""


def water_input(dispersion=False):
    return """&GLOBAL
  PROJECT benchmark
  PRINT_LEVEL MEDIUM
  RUN_TYPE ENERGY_FORCE
&END GLOBAL
&FORCE_EVAL
  METHOD QS
  &DFT
    BASIS_SET_FILE_NAME BASIS_MOLOPT
    POTENTIAL_FILE_NAME GTH_POTENTIALS
    &MGRID
      CUTOFF 300
      REL_CUTOFF 40
    &END MGRID
    &POISSON
      PERIODIC NONE
      POISSON_SOLVER MT
    &END POISSON
    &QS
      METHOD GPW
      EPS_DEFAULT 1.0E-12
    &END QS
    &SCF
      EPS_SCF 1.0E-9
      MAX_SCF 100
      SCF_GUESS ATOMIC
      &OT ON
        MINIMIZER DIIS
        PRECONDITIONER FULL_ALL
      &END OT
      &OUTER_SCF
        EPS_SCF 1.0E-9
        MAX_SCF 10
      &END OUTER_SCF
    &END SCF
    &XC
      &XC_FUNCTIONAL PBE
      &END XC_FUNCTIONAL
""" + (DFTD4_SECTION if dispersion else "") + """    &END XC
  &END DFT
""" + FORCE_PRINT + """  &SUBSYS
    &CELL
      ABC 12.0 12.0 12.0
      PERIODIC NONE
    &END CELL
    &COORD
""" + WATER_COORDINATES + """    &END COORD
    &KIND H
      BASIS_SET DZVP-MOLOPT-SR-GTH
      POTENTIAL GTH-PBE-q1
    &END KIND
    &KIND O
      BASIS_SET DZVP-MOLOPT-SR-GTH
      POTENTIAL GTH-PBE-q6
    &END KIND
  &END SUBSYS
&END FORCE_EVAL
"""


# tests/xTB/regtest-tblite-gfn2-1/CH2O_gfn2.inp at FIXTURE_REVISION;
# ENERGY -> ENERGY_FORCE and explicit force print are the only physics-neutral
# changes. Its TEST_FILES.toml provides the additional independent energy gate.
TBLITE_INPUT = """&GLOBAL
  PRINT_LEVEL HIGH
  PROJECT benchmark
  RUN_TYPE ENERGY_FORCE
&END GLOBAL
&FORCE_EVAL
  &DFT
    &QS
      METHOD xTB
      &XTB
        GFN_TYPE TBLITE
        &TBLITE
          METHOD GFN2
        &END TBLITE
      &END XTB
    &END QS
    &SCF
      EPS_SCF 1.e-8
      MAX_SCF 100
      SCF_GUESS MOPAC
      &MIXING
        ALPHA 0.2
        METHOD DIRECT_P_MIXING
      &END MIXING
    &END SCF
  &END DFT
""" + FORCE_PRINT + """  &SUBSYS
    &CELL
      ABC 20.0 20.0 20.0
      PERIODIC NONE
    &END CELL
    &COORD
      O 0.051368 0.000000 0.000000
      C 1.278612 0.000000 0.000000
      H 1.870460 0.939607 0.000000
      H 1.870460 -0.939607 0.000000
    &END COORD
  &END SUBSYS
&END FORCE_EVAL
"""

# CP2K 2026.1 requires an integer GFN_TYPE and selects the TBLITE backend
# through the section's boolean. CP2K 2026.2 requires GFN_TYPE TBLITE instead.
# These are the only approved syntax differences; all scientific parameters
# remain byte-identical after normalizing those two backend-selection lines.
TBLITE_BASELINE_INPUT = TBLITE_INPUT.replace("        GFN_TYPE TBLITE\n", "").replace(
    "        &TBLITE\n", "        &TBLITE T\n")


def water64_input():
    """Versioned synthetic 64-water workload, not an upstream reference energy."""
    coordinates = []
    for x in range(4):
        for y in range(4):
            for z in range(4):
                for atom, dx, dy in (("O", 0.0, 0.0), ("H", 0.9572, 0.0), ("H", -0.239987, 0.927297)):
                    coordinates.append(f"      {atom} {0.8 + 3.1*x + dx:.6f} {0.8 + 3.1*y + dy:.6f} {0.8 + 3.1*z:.6f}\n")
    text = water_input().replace(WATER_COORDINATES, "".join(coordinates))
    text = text.replace("ABC 12.0 12.0 12.0", "ABC 12.4 12.4 12.4")
    return text.replace("PERIODIC NONE", "PERIODIC XYZ").replace("POISSON_SOLVER MT", "POISSON_SOLVER PERIODIC")


def water_elpa_input():
    # The default 2% timing threshold hides the eigensolver on this small case.
    # Identical diagnostic controls on both versions expose actual ELPA calls
    # without changing the physical or SCF settings.
    text = water_input().replace("  RUN_TYPE ENERGY_FORCE", "  RUN_TYPE ENERGY_FORCE\n  PREFERRED_DIAG_LIBRARY ELPA\n  &TIMINGS\n    THRESHOLD 0\n    TIMINGS_LEVEL 1\n  &END TIMINGS")
    start = text.index("      &OT ON\n")
    end = text.index("      &END OT\n", start) + len("      &END OT\n")
    return text[:start] + "      &DIAGONALIZATION\n        ALGORITHM STANDARD\n      &END DIAGONALIZATION\n" + text[end:]


CASES = {
    "water-gpw": {"input": water_input(), "elements": ["O", "O", "H", "H", "H", "H"]},
    "water-dftd4": {"input": water_input(True), "elements": ["O", "O", "H", "H", "H", "H"]},
    "ch2o-tblite": {"input": TBLITE_INPUT, "elements": ["O", "C", "H", "H"]},
    "water-elpa": {"input": water_elpa_input(), "elements": ["O", "O", "H", "H", "H", "H"]},
    "water64-gpw": {"input": water64_input(), "elements": ["O", "H", "H"] * 64},
}


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def case_input(case, kind="candidate"):
    return TBLITE_BASELINE_INPUT if case == "ch2o-tblite" and kind == "baseline" else CASES[case]["input"]


def syntax_contract():
    normalized = TBLITE_BASELINE_INPUT.replace("        &TBLITE T\n", "        GFN_TYPE TBLITE\n        &TBLITE\n")
    if normalized != TBLITE_INPUT:
        raise ValueError("TBLITE baseline fixture differs beyond approved version syntax")
    return {"case": "ch2o-tblite", "same_bytes": False,
            "candidate_backend_selector": "GFN_TYPE TBLITE", "baseline_backend_selector": "TBLITE T section",
            "unchanged": ["GFN2 method", "charge/spin defaults", "SCF thresholds", "geometry", "forces"],
            "performance_comparison": False}


def fixture_hashes(case, kind="candidate"):
    return {"input.inp": digest(case_input(case, kind)),
            **(DATA_HASHES if case.startswith("water") else {})}


def number(value):
    try:
        result = float(value.replace("D", "E").replace("d", "e"))
    except ValueError as exc:
        raise ValueError("invalid CP2K numerical result") from exc
    if not math.isfinite(result):
        raise ValueError("nonfinite CP2K numerical result")
    return result


def parse_version(text):
    versions = re.findall(r"^\s*CP2K version\s+(.+)$", text, re.M)
    flags = re.findall(r"^\s*cp2kflags:\s*(.*)$", text, re.M)
    revisions = re.findall(r"^\s*Source code revision\s+(.+)$", text, re.M)
    if (not versions or not flags or not revisions or len(set(versions)) != 1 or
            len(set(flags)) != 1 or len(set(revisions)) != 1):
        raise ValueError("missing or inconsistent CP2K version/flags")
    return {"version": versions[0], "flags": sorted(set(flags[0].split())),
            "revision": revisions[0] if revisions else "unknown"}


def feature_comparison(candidate, baseline, equivalences=None, approved_exceptions=None):
    """No blanket version waiver; every exception is explicit in the proof."""
    missing = set(baseline["flags"]) - set(candidate["flags"])
    equivalent = {}
    if "xsmm" in missing and {"libxs", "libxsmm"}.issubset(candidate["flags"]):
        equivalent["xsmm"] = "libxs+libxsmm (CP2K 2026.2 renamed flags)"
        missing.remove("xsmm")
    # A caller must validate source evidence before passing this equivalence.
    if "libgrpp" in missing and (equivalences or {}).get("libgrpp") == "builtin_with_source_evidence":
        equivalent["libgrpp"] = "builtin_with_source_evidence"
        missing.remove("libgrpp")
    exceptions = {}
    if "quip" in missing and (approved_exceptions or {}).get("quip") == "user-approved: upstream removed in CP2K 2026":
        exceptions["quip"] = approved_exceptions["quip"]
        missing.remove("quip")
    if missing:
        raise ValueError("candidate lacks baseline features: " + ", ".join(sorted(missing)))
    return {"missing": [], "equivalent": equivalent, "approved_exceptions": exceptions}


def parse_science(log, forces, elapsed, case):
    if ("PROGRAM ENDED AT" not in log or not re.search(r"SCF run converged", log) or
            re.search(r"SCF run NOT converged|\bABORT\b", log, re.I)):
        raise ValueError("CP2K calculation did not finish with converged SCF")
    energies = re.findall(r"ENERGY\|\s+Total FORCE_EVAL.*?energy\s+\[hartree\]\s+(\S+)", log)
    if len(energies) != 1:
        raise ValueError("expected exactly one ENERGY_FORCE energy")
    energy = number(energies[0])
    if case == "ch2o-tblite" and abs(energy - TBLITE_REFERENCE_HA) > TBLITE_REFERENCE_TOLERANCE_HA:
        raise ValueError("TBLITE energy differs from pinned upstream reference")
    if forces.count("ATOMIC FORCES in [a.u.]") != 1 or "SUM OF ATOMIC FORCES" not in forces:
        raise ValueError("missing unique atomic-unit force block")
    rows = re.findall(r"^\s*(\d+)\s+(\d+)\s+([A-Za-z]+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$", forces, re.M)
    if (len(rows) != len(CASES[case]["elements"]) or
            [row[0] for row in rows] != [str(i + 1) for i in range(len(rows))] or
            [row[2] for row in rows] != CASES[case]["elements"]):
        raise ValueError("force atom count/order/elements differ from fixture")
    vectors = [[number(value) for value in row[3:]] for row in rows]
    timings = re.findall(r"^\s*CP2K\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+\d+)?\s*$", log, re.M)
    if len(timings) != 1:
        raise ValueError("missing unique CP2K timing row")
    wall = number(elapsed.strip())
    total = number(timings[0][5])  # TOTAL TIME MAXIMUM, before optional MAXRANK
    if wall <= 0 or total <= 0:
        raise ValueError("timings must be positive")
    if case == "water64-gpw" and total < 2:
        raise ValueError("GPW performance workload too short to measure compute time")
    elpa_calls = re.findall(r"^\s*cp_fm_diag_elpa(?:_base)?\s+(\d+)\s+", log, re.M)
    if case == "water-elpa" and not any(int(count) > 0 for count in elpa_calls):
        raise ValueError("ELPA eigensolver path was not executed")
    dispersion = None
    if case == "water-dftd4":
        values = re.findall(r"Dispersion energy:\s*(\S+)", log)
        if not values:
            raise ValueError("DFTD4 case has no dispersion-energy evidence")
        dispersion = number(values[-1])
        if dispersion == 0:
            raise ValueError("DFTD4 dispersion energy is zero")
    return {"energy_ha": energy, "forces_ha_bohr": vectors,
            "elapsed_seconds": wall, "cp2k_seconds": total,
            "dispersion_ha": dispersion}


def compare_science(candidate, reference):
    energy_delta = abs(candidate["energy_ha"] - reference["energy_ha"])
    if len(candidate["forces_ha_bohr"]) != len(reference["forces_ha_bohr"]):
        raise ValueError("scientific reference has a different number of atoms")
    force_delta = max(abs(a - b) for va, vb in zip(candidate["forces_ha_bohr"], reference["forces_ha_bohr"])
                      for a, b in zip(va, vb))
    if energy_delta > ENERGY_TOLERANCE_HA or force_delta > FORCE_TOLERANCE_HA_BOHR:
        raise ValueError("CP2K energy/force scientific tolerance exceeded")
    if candidate["dispersion_ha"] is not None:
        if reference["dispersion_ha"] is None or abs(candidate["dispersion_ha"] - reference["dispersion_ha"]) > ENERGY_TOLERANCE_HA:
            raise ValueError("DFTD4 dispersion differs from reference")
    return {"energy_delta_ha": energy_delta, "max_force_delta_ha_bohr": force_delta}


def call(argv, **kwargs):
    return subprocess.run([str(value) for value in argv], check=True, text=True, **kwargs)


def run_dir(run_id):
    path = ROOT / "runtime-tests" / safe_name(run_id)
    if path.resolve() != path:
        raise ValueError("benchmark directory must not be a symlink")
    return path


def resources(target):
    if target not in BENCHMARK_TARGETS:
        raise ValueError("unregistered CP2K benchmark target")
    cpu = TARGETS[target]["gpus"] == 0
    return {"nodes": 2, "gpus_per_node": 0 if cpu else 1,
            "ranks_per_node": 8 if cpu else 1, "cpus_per_task": 2 if cpu else 1,
            "ranks": 16 if cpu else 2}


def baseline_cases(target):
    # The inspected 2025.1 CPU baseline predates CP2K's TBLITE/DFTD4 interfaces.
    return {"water-gpw", "water-elpa", "water64-gpw"} if TARGETS[target]["gpus"] == 0 else set(CASES)


def schedule(target):
    plan = []
    for case in CASES:
        for repetition in range(4):  # 0 = warm-up; 1..3 = measured
            order = ("candidate", "baseline") if repetition % 2 == 0 else ("baseline", "candidate")
            for kind in order:
                if kind == "candidate" or case in baseline_cases(target):
                    plan.append((case, repetition, kind))
    return plan


def stage_fixtures(task):
    syntax_contract()
    data = {}
    for name, expected in DATA_HASHES.items():
        content = subprocess.check_output([
            "git", f"--git-dir={ROOT / 'cache/repositories/cp2k'}", "show",
            f"{FIXTURE_REVISION}:data/{name}"])
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f"pinned benchmark data checksum mismatch: {name}")
        data[name] = content
    for case, spec in CASES.items():
        for kind in ("candidate", "baseline"):
            folder = task / "fixtures" / case / kind
            folder.mkdir(parents=True)
            (folder / "input.inp").write_text(case_input(case, kind))
            for name in fixture_hashes(case, kind).keys() - {"input.inp"}:
                (folder / name).write_bytes(data[name])


def build_artifact(build_run_id, version, target):
    expected = ROOT / "containers/software/cp2k" / safe_name(version) / safe_name(target) / f"{safe_name(build_run_id)}.sif"
    reference = ROOT / "runs" / build_run_id / "artifact.path"
    if (not reference.is_file() or reference.is_symlink() or
            reference.read_text().strip() != str(expected) or
            not expected.is_file() or expected.is_symlink() or expected.resolve() != expected):
        raise ValueError("CP2K build artifact is missing or untrusted")
    sidecar = expected.with_suffix(".json")
    if not sidecar.is_file() or sidecar.is_symlink():
        raise ValueError("CP2K artifact manifest is missing")
    manifest = json.loads(sidecar.read_text())
    if (manifest.get("build_verified") is not True or manifest.get("artifact") != str(expected) or
            manifest.get("version") != version or manifest.get("target") != target or
            manifest.get("sha256") != checksum(expected) or not manifest.get("recipe_sha256")):
        raise ValueError("CP2K artifact manifest is invalid")
    return expected, manifest


RANK_WRAPPER = r'''#!/usr/bin/env bash
set -euo pipefail
rank=${OMPI_COMM_WORLD_RANK:?}
local_rank=${OMPI_COMM_WORLD_LOCAL_RANK:?}
[[ "$rank" =~ ^[0-9]+$ && "$local_rank" =~ ^[0-9]+$ ]] || exit 2
host=$(hostname -s)
affinity=$(awk '/^Cpus_allowed_list:/ { print $2 }' /proc/self/status)
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$host" "$rank" "$local_rank" \
  "${OMP_NUM_THREADS:?}" "$affinity" "${CUDA_VISIBLE_DEVICES:-}" "${SLURM_JOB_ID:?}" \
  > "$SAI_BENCH_RESOURCES/rank-$rank.tsv"
if [[ "$SAI_BENCH_KIND" == baseline ]]; then
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$host" "$rank" "$SAI_BENCH_TARGET" \
    "system:$SAI_BENCH_BINARY" "${CUDA_VISIBLE_DEVICES:-}" "${MPI_HOME:?}" \
    > "$SAI_CP2K_TRACE_DIR/rank-$rank.tsv"
fi
exec "$SAI_BENCH_BINARY" "$@"
'''


def render_job(args):
    q = shlex.quote
    target = TARGETS[args.target]
    allocation = resources(args.target)
    cpu = allocation["gpus_per_node"] == 0
    task = run_dir(args.run_id)
    baseline = CPU_BASELINE if cpu else GPU_BASELINE
    resource_lines = (["#SBATCH --ntasks-per-node=8", "#SBATCH --cpus-per-task=2"] if cpu else
                      ["#SBATCH --ntasks-per-node=1", "#SBATCH --gpus-per-node=1"])
    mapping = (["export MAP_OPT=ppr:8:node:pe=2", "export OMP_NUM_THREADS=2"] if cpu else
               [f"source /opt/sai_config/mps_mapping.d/{target['partition']}.bash", "export OMP_NUM_THREADS=1"])
    prefix = f"/opt/software/cp2k/{args.version}/{args.target}"
    lines = [
        "#!/usr/bin/env bash",
        f"#SBATCH --job-name=cp2k-benchmark-{safe_name(args.run_id)}",
        f"#SBATCH --partition={target['partition']}", f"#SBATCH --qos={target['qos']}",
        "#SBATCH --nodes=2", f"#SBATCH --ntasks={allocation['ranks']}",
        *resource_lines, f"#SBATCH --time={int(args.minutes)}",
        f"#SBATCH --output={task}/results/slurm-%j.log", "#SBATCH --export=NIL",
        "set -euo pipefail",
        f"export HOME={q(str(Path.home()))}",
        "export USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?}",
        "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        'export LD_LIBRARY_PATH="" LD_PRELOAD=""',
        "unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH",
        f"export SAI_SOFTWARE_ROOT={q(str(ROOT))}",
        f"export SAI_CP2K_VERSION={q(args.version)} SAI_CP2K_IMAGE={q(str(args.artifact))}",
        f"export TMPDIR={q(str(ROOT / 'runtime/cp2k-benchmarks' / args.run_id / 'mpi'))}",
        f"export APPTAINER_TMPDIR={q(str(task / 'apptainer-runtime'))}",
        f"export APPTAINER_CACHEDIR={q(str(task / 'apptainer-cache'))}",
        'mkdir -p "$TMPDIR" "$APPTAINER_TMPDIR" "$APPTAINER_CACHEDIR"',
        "source /etc/profile.d/lmod.sh",
        "export SLURM_EXPORT_ENV=ALL",
        "export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher",
        f"cd {q(str(task))}",
        'scontrol show job -o "$SLURM_JOB_ID" > results/allocation.txt',
        'scontrol show hostnames "$SLURM_JOB_NODELIST" > results/nodes.txt',
        "load_runner() {",
        "  module purge",
        "  module use /opt/modules/modulefiles/devtools /opt/modules/modulefiles/apps",
        "  module load apptainer/1.4.4",
        '  if [[ "$1" == baseline ]]; then',
        f"    module load {q(baseline)}",
        '    export SAI_BENCH_BINARY=$(readlink -f -- "$(command -v cp2k.psmp)")',
        "  else",
        "    module load openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto",
        f"    export SAI_BENCH_BINARY={q(str(args.launcher))}",
        "  fi",
        *["  " + line for line in mapping],
        '  export SAI_BENCH_KIND="$1"',
        f"  export SAI_BENCH_TARGET={q(args.target)}",
        "}",
        "describe_runner() {",
        '  local kind="$1"',
        '  load_runner "$kind"',
        '  mkdir -p "results/$kind-version/ranks" "results/$kind-version/resources"',
        f'  export SAI_CP2K_TRACE_DIR={q(str(task))}/results/$kind-version/ranks',
        f'  export SAI_BENCH_RESOURCES={q(str(task))}/results/$kind-version/resources',
        '  readlink -f "$SAI_BENCH_BINARY" > "results/$kind-version/binary.txt"',
        '  sha256sum "$SAI_BENCH_BINARY" > "results/$kind-version/binary.sha256"',
        '  command -v mpirun > "results/$kind-version/mpirun.txt"',
        '  mpirun --version > "results/$kind-version/mpi-version.txt"',
        '  module -t list > "results/$kind-version/modules.txt" 2>&1',
        f'  mpirun -np {allocation["ranks"]} --map-by "$MAP_OPT" --report-bindings ./rank-exec.sh --version > "results/$kind-version/version.log" 2>&1',
        "}",
        "describe_runner candidate", "describe_runner baseline",
        "run_one() {",
        '  local case="$1" repetition="$2" kind="$3"',
        '  local work="cases/$case/$repetition-$kind"',
        '  load_runner "$kind"',
        '  mkdir -p "cases/$case"',
        '  mkdir "$work"',  # Never reuse a prior SCF directory, even on retry.
        '  cp -- "fixtures/$case/$kind/"* "$work/"',
        '  mkdir "$work/ranks" "$work/resources"',
        f'  export SAI_CP2K_TRACE_DIR={q(str(task))}/$work/ranks',
        f'  export SAI_BENCH_RESOURCES={q(str(task))}/$work/resources',
        '  printf "%s\\t%s\\t%s\\n" "$case" "$repetition" "$kind" >> results/run-order.tsv',
        '  readlink -f "$SAI_BENCH_BINARY" > "$work/binary.txt"',
        '  sha256sum "$SAI_BENCH_BINARY" > "$work/binary.sha256"',
        '  command -v mpirun > "$work/mpirun.txt"',
        '  printf "%s\\n" "$MAP_OPT" > "$work/map.txt"',
        '  ( cd "$work"',
        f'    /usr/bin/time -f "%e" -o elapsed.txt mpirun -np {allocation["ranks"]} --map-by "$MAP_OPT" --report-bindings {q(str(task / "rank-exec.sh"))} -i input.inp > cp2k.log 2>&1',
        "  )",
        "}",
        *[f"run_one {case} {repetition} {kind}" for case, repetition, kind in schedule(args.target)],
        "echo CP2K_SCIENTIFIC_BENCHMARK_FINISHED",
    ]
    return "\n".join(lines) + "\n"


def checked_read(task, relative, files):
    path = Path(task) / relative
    if not path.is_file() or path.is_symlink() or path.resolve() != path:
        raise ValueError(f"missing or untrusted benchmark evidence: {relative}")
    files[str(relative)] = checksum(path)
    return path.read_text()


def verify_rank_evidence(task, folder, request, hosts, files, *, binary):
    allocation = resources(request["target"])
    expected_names = {f"rank-{rank}.tsv" for rank in range(allocation["ranks"])}
    for name in ("ranks", "resources"):
        if {path.name for path in (task / folder / name).glob("rank-*.tsv")} != expected_names:
            raise ValueError("benchmark rank/resource trace set does not match allocation")
    placements = Counter()
    bindings = {}
    host_cpus = {}
    local_ranks = {}
    mpi_roots = set()
    isa = TARGETS[request["target"]]["dependency_isa"]
    for rank in range(allocation["ranks"]):
        values = checked_read(task, f"{folder}/ranks/rank-{rank}.tsv", files).rstrip("\n").split("\t")
        if len(values) != 6:
            raise ValueError("malformed CP2K rank trace")
        host, actual_rank, target, image, gpu, mpi = values
        if (host not in hosts or actual_rank != str(rank) or target != request["target"] or
                image != binary or not mpi.startswith("/") or not mpi.endswith("-" + isa)):
            raise ValueError("rank does not match pinned image/target/MPI ISA/allocation")
        detail = checked_read(task, f"{folder}/resources/rank-{rank}.tsv", files).rstrip("\n").split("\t")
        if (len(detail) != 7 or detail[0] != host or detail[1] != str(rank) or
                not detail[2].isdigit() or detail[3] != str(allocation["cpus_per_task"]) or
                not re.fullmatch(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*", detail[4]) or
                detail[5] != gpu or detail[6] != request["job"]):
            raise ValueError("rank resource evidence is inconsistent")
        if allocation["gpus_per_node"] and (not gpu or "," in gpu or gpu == "-1"):
            raise ValueError("GPU benchmark requires one visible GPU per rank")
        if not allocation["gpus_per_node"] and gpu:
            raise ValueError("CPU benchmark unexpectedly exposes a GPU")
        cpus = set()
        for span in detail[4].split(","):
            limits = [int(value) for value in span.split("-")]
            if limits[0] > limits[-1]:
                raise ValueError("invalid affinity range")
            cpus.update(range(limits[0], limits[-1] + 1))
        if len(cpus) < allocation["cpus_per_task"]:
            raise ValueError("rank has insufficient bound CPUs for OpenMP threads")
        if host_cpus.setdefault(host, set()) & cpus:
            raise ValueError("benchmark MPI ranks overlap CPU affinity on the same node")
        host_cpus[host].update(cpus)
        bindings[str(rank)] = {"host": host, "local_rank": int(detail[2]),
                               "cpus": sorted(cpus), "cuda_visible_devices": gpu}
        local_ranks.setdefault(host, []).append(int(detail[2]))
        mpi_roots.add(mpi)
        placements[host] += 1
    if (placements != Counter({host: allocation["ranks_per_node"] for host in hosts}) or
            any(sorted(values) != list(range(allocation["ranks_per_node"])) for values in local_ranks.values()) or
            len(mpi_roots) != 1):
        raise ValueError("rank distribution differs from the two-node resource contract")
    return {"nodes": dict(placements), "mpi_home": next(iter(mpi_roots)), "bindings": bindings}


def accepted_reference(run_id, expected_sha256=None):
    task = run_dir(run_id)
    files = {}
    request = json.loads(checked_read(task, "request.json", files))
    status = json.loads(checked_read(task, "results/status.json", files))
    stored = json.loads(checked_read(task, "results/evidence.json", files))
    if (not request.get("gpus_per_node") or status.get("verified") is not True or
            status.get("job") != request.get("job") or status.get("state") != "COMPLETED" or
            status.get("exit_code") != "0:0" or
            (expected_sha256 is not None and files["results/evidence.json"] != expected_sha256)):
        raise ValueError("GPU scientific reference status or identity is not accepted")
    evidence = verify_evidence(task, request, allow_reference=False)
    if evidence != stored:
        raise ValueError("GPU scientific reference raw evidence changed")
    return evidence, files["results/evidence.json"]


def verify_evidence(task, request, *, allow_reference=True):
    """Reparse raw logs/forces/resources, including every warm-up; never trust a marker alone."""
    task = Path(task)
    files = {}
    read = lambda name: checked_read(task, name, files)
    artifact, manifest = build_artifact(request["build_run_id"], request["version"], request["target"])
    if (str(artifact) != request["artifact"] or manifest["sha256"] != request["artifact_sha256"] or
            manifest["recipe_sha256"] != request["recipe_sha256"] or manifest["source_sha"] != request["source_sha"]):
        raise ValueError("benchmark artifact/source/recipe identity changed")
    # Each CI target has an immutable controller snapshot. A CPU run can
    # reverify a GPU reference from another snapshot when the trusted launcher
    # bytes and verifier contract match, not merely the directory name.
    launcher = Path(request["launcher"])
    try:
        launcher.relative_to(ROOT / "controller")
    except ValueError as exc:
        raise ValueError("benchmark launcher is outside trusted controllers") from exc
    if (not launcher.is_file() or launcher.is_symlink() or launcher.resolve() != launcher or
            checksum(launcher) != request["launcher_sha256"] or
            checksum(launcher) != checksum(CONTROL / "cp2k") or
            checksum(Path(__file__).resolve()) != request["controller_sha256"]):
        raise ValueError("benchmark controller or launcher changed")
    if digest(read("job.sbatch")) != request["job_script_sha256"]:
        raise ValueError("benchmark job script changed")
    if read("job.sbatch") != render_job(argparse.Namespace(**request)):
        raise ValueError("benchmark request no longer matches trusted job rendering")
    if read("rank-exec.sh") != RANK_WRAPPER:
        raise ValueError("benchmark rank wrapper changed")
    job = read("job.id").strip()
    if not job.isdigit() or request.get("job") != job:
        raise ValueError("benchmark job id is inconsistent")
    for name, value in resources(request["target"]).items():
        if request.get(name) != value:
            raise ValueError("benchmark resources changed")
    expected_baseline = CPU_BASELINE if not request["gpus_per_node"] else GPU_BASELINE
    if request.get("baseline_module") != expected_baseline:
        raise ValueError("benchmark baseline module changed")
    hosts = read("results/nodes.txt").splitlines()
    if len(hosts) != 2 or len(set(hosts)) != 2 or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", host) for host in hosts):
        raise ValueError("benchmark did not use exactly two allocation nodes")
    allocation = dict(re.findall(r"(?:^|\s)([A-Za-z][A-Za-z0-9/]*)=([^\s]+)", read("results/allocation.txt")))
    if (allocation.get("JobId") != job or allocation.get("Partition") != TARGETS[request["target"]]["partition"] or
            allocation.get("NumNodes") != "2" or allocation.get("NumTasks") != str(request["ranks"])):
        raise ValueError("Slurm allocation does not match requested resources")
    if not request["gpus_per_node"] and (allocation.get("CPUs/Task") != "2" or int(allocation.get("NumCPUs", "0")) < 32):
        raise ValueError("Slurm CPU resources changed")
    if request["gpus_per_node"] and not re.search(r"(?:^|,)gres/gpu=2(?:,|$)", allocation.get("AllocTRES", "")):
        raise ValueError("Slurm allocation did not allocate exactly two GPUs")
    if "CP2K_SCIENTIFIC_BENCHMARK_FINISHED" not in read(f"results/slurm-{job}.log"):
        raise ValueError("benchmark completion marker is absent")
    plan = schedule(request["target"])
    if read("results/run-order.tsv") != "".join(f"{case}\t{repetition}\t{kind}\n" for case, repetition, kind in plan):
        raise ValueError("warm-up/repetition/alternating execution order changed")
    versions = {}
    binaries = {}
    mpi = {}
    for kind in ("candidate", "baseline"):
        folder = f"results/{kind}-version"
        versions[kind] = parse_version(read(f"{folder}/version.log"))
        binaries[kind] = read(f"{folder}/binary.txt").strip()
        if not binaries[kind].startswith("/"):
            raise ValueError("benchmark executable must have an absolute path")
        binary_digest = read(f"{folder}/binary.sha256").split()
        if len(binary_digest) != 2 or not re.fullmatch(r"[0-9a-f]{64}", binary_digest[0]):
            raise ValueError("benchmark executable digest missing")
        if kind == "candidate" and (binaries[kind] != request["launcher"] or binary_digest[0] != request["launcher_sha256"]):
            raise ValueError("benchmark did not use the pinned candidate launcher")
        if kind == "baseline" and not binaries[kind].startswith("/opt/apps/cp2k/"):
            raise ValueError("benchmark baseline is not the site installation")
        mpi[kind] = {"path": read(f"{folder}/mpirun.txt").strip(),
                     "version": read(f"{folder}/mpi-version.txt")}
        modules = read(f"{folder}/modules.txt")
        if kind == "baseline" and expected_baseline not in modules:
            raise ValueError("baseline module was not loaded")
        expected_image = request["artifact"] if kind == "candidate" else "system:" + binaries[kind]
        mpi[kind].update(verify_rank_evidence(task, folder, request, hosts, files, binary=expected_image))
    if mpi["candidate"]["bindings"] != mpi["baseline"]["bindings"]:
        raise ValueError("candidate and baseline have different actual CPU/GPU bindings")
    if not request["gpus_per_node"] and any(flag in versions["baseline"]["flags"] for flag in ("offload_cuda", "dbcsr_acc", "cusolvermp")):
        raise ValueError("a CUDA baseline cannot stand in for the CPU baseline")
    expected_version = "2026.1" if request["gpus_per_node"] else "2025.1"
    if versions["baseline"]["version"] != expected_version:
        raise ValueError("site baseline version does not match the pinned module")
    if request["gpus_per_node"] and not {"offload_cuda", "dbcsr_acc"}.issubset(versions["baseline"]["flags"]):
        raise ValueError("GPU baseline lacks CUDA/DBCSR offload support")
    changes = json.loads(read("results/upstream-feature-changes.json"))
    if (files["results/upstream-feature-changes.json"] != request["feature_changes_sha256"] or
            changes.get("source_sha") != request["source_sha"] or
            changes.get("libgrpp") != "builtin" or changes.get("quip") != "upstream_removed" or
            changes.get("approved_parity_exceptions") != APPROVED_EXCEPTIONS or
            set(changes.get("evidence", {})) != {"src/CMakeLists.txt", "src/libgrpp_integrals.F", "CMakeLists.txt"} or
            any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in changes["evidence"].values())):
        raise ValueError("upstream feature equivalence evidence is invalid")
    # The source checks are made by the trusted build recipe before sealing the
    # artifact. The one QUIP exception was explicitly approved by the user; it
    # is reported, not described as an unqualified feature superset.
    parity = feature_comparison(versions["candidate"], versions["baseline"],
                                {"libgrpp": "builtin_with_source_evidence"},
                                {"quip": "user-approved: upstream removed in CP2K 2026"})
    if not {"tblite", "libdftd4"}.issubset(versions["candidate"]["flags"]):
        raise ValueError("candidate lacks required TBLITE/DFTD4 features")
    references = None
    reference_proof = None
    if set(CASES) - baseline_cases(request["target"]):
        if not allow_reference or not request.get("reference_run_id"):
            raise ValueError("CPU cases without a site baseline require verified GPU reference evidence")
        reference_evidence, reference_hash = accepted_reference(
            request["reference_run_id"], request.get("reference_evidence_sha256"))
        references = reference_evidence["samples"]
        reference_proof = {"run_id": request["reference_run_id"], "evidence_sha256": reference_hash,
                           "use": "scientific reference only; not a CPU performance baseline"}
    samples = {}
    placement = {}
    for case, repetition, kind in plan:
        folder = f"cases/{case}/{repetition}-{kind}"
        for name, expected in fixture_hashes(case, kind).items():
            read(f"{folder}/{name}")
            if files[f"{folder}/{name}"] != expected:
                raise ValueError("benchmark input/data differs from the pinned fixture")
        if read(f"{folder}/binary.txt").strip() != binaries[kind] or read(f"{folder}/binary.sha256") != read(f"results/{kind}-version/binary.sha256"):
            raise ValueError("executable changed between repetitions")
        if read(f"{folder}/mpirun.txt").strip() != mpi[kind]["path"]:
            raise ValueError("MPI launcher changed between repetitions")
        mapping = read(f"{folder}/map.txt").strip()
        if not mapping or (not request["gpus_per_node"] and mapping != "ppr:8:node:pe=2"):
            raise ValueError("invalid MPI resource mapping")
        if case == next(iter(CASES)) and repetition == 0:
            placement[kind] = mapping
        elif mapping != placement[kind]:
            raise ValueError("resource mapping changed between repetitions")
        expected_image = request["artifact"] if kind == "candidate" else "system:" + binaries[kind]
        rank_proof = verify_rank_evidence(task, folder, request, hosts, files, binary=expected_image)
        if rank_proof["bindings"] != mpi[kind]["bindings"]:
            raise ValueError("actual rank CPU/GPU bindings changed between repetitions")
        samples[f"{case}/{repetition}-{kind}"] = parse_science(
            read(f"{folder}/cp2k.log"), read(f"{folder}/forces.xyz"), read(f"{folder}/elapsed.txt"), case)
    if placement["candidate"] != placement["baseline"]:
        raise ValueError("candidate and baseline resource mappings differ")
    comparisons = {}
    summaries = {}
    for case in CASES:
        for repetition in range(4):
            candidate = samples[f"{case}/{repetition}-candidate"]
            baseline = (samples if case in baseline_cases(request["target"]) else references)[f"{case}/{repetition}-baseline"]
            comparisons[f"{case}/{repetition}"] = compare_science(candidate, baseline)
        summary = {"warmups": 1, "measured_repetitions": 3,
                   "baseline_available": case in baseline_cases(request["target"]),
                   "performance_comparison": case != "ch2o-tblite"}
        for clock in ("elapsed_seconds", "cp2k_seconds"):
            candidate = [samples[f"{case}/{r}-candidate"][clock] for r in range(1, 4)]
            summary["candidate_" + clock] = {"samples": candidate, "median": statistics.median(candidate)}
            if summary["baseline_available"]:
                baseline = [samples[f"{case}/{r}-baseline"][clock] for r in range(1, 4)]
                summary["baseline_" + clock] = {"samples": baseline, "median": statistics.median(baseline)}
                if summary["performance_comparison"]:
                    summary["baseline_over_candidate_" + clock] = statistics.median(baseline) / statistics.median(candidate)
        summaries[case] = summary
    return {"schema": 1, "job": job, "artifact_sha256": request["artifact_sha256"],
            "fixture_revision": FIXTURE_REVISION,
            "input_hashes": {case: {kind: fixture_hashes(case, kind) for kind in ("candidate", "baseline")} for case in CASES},
            "version_syntax_contract": syntax_contract(),
            "energy_tolerance_ha": ENERGY_TOLERANCE_HA, "force_tolerance_ha_bohr": FORCE_TOLERANCE_HA_BOHR,
            "versions": versions, "feature_parity": parity, "mpi": mpi, "nodes": hosts,
            "reference": reference_proof, "samples": samples, "comparisons": comparisons,
            "timings": summaries, "files": files,
            "performance_scope": "water64-gpw: fixed 64-water periodic GPW compute workload; other cases are correctness/latency probes; no universal production speed claim"}


def submit(args):
    safe_name(args.run_id)
    safe_name(args.version)
    if not 5 <= args.minutes <= 120:
        raise ValueError("benchmark wall time outside accepted bounds")
    allocation = resources(args.target)
    artifact, manifest = build_artifact(args.build_run_id, args.version, args.target)
    launcher = CONTROL / "cp2k"
    if not launcher.is_file() or launcher.is_symlink() or not os.access(launcher, os.X_OK):
        raise ValueError("trusted CP2K launcher is missing")
    reference_sha256 = None
    reference = getattr(args, "reference_run_id", None)
    if not allocation["gpus_per_node"]:
        if not reference:
            raise ValueError("CPU TBLITE/DFTD4 require --reference-run-id from an accepted GPU comparison")
        _, reference_sha256 = accepted_reference(reference)
    elif reference:
        raise ValueError("GPU benchmarks must use their own allocation's site baseline")
    task = run_dir(args.run_id)
    if task.exists():
        raise ValueError("benchmark already exists; choose a fresh run id")
    for name in ("results", "apptainer-runtime", "apptainer-cache"):
        (task / name).mkdir(parents=True)
    stage_fixtures(task)
    # Copy generated metadata from the exact sealed candidate, without running
    # a GPU executable or importing an installation tree onto the host.
    prefix = f"/opt/software/cp2k/{args.version}/{args.target}"
    env = dict(os.environ, TMPDIR=str(task / "apptainer-runtime"),
               APPTAINER_TMPDIR=str(task / "apptainer-runtime"),
               APPTAINER_CACHEDIR=str(task / "apptainer-cache"))
    result = call(["/usr/bin/bash", "--noprofile", "--norc", "-c",
                   'set -e; source /etc/profile.d/lmod.sh; module load apptainer/1.4.4; exec "$@"',
                   "bash", "apptainer", "exec", "--cleanenv", "--containall", "--no-home",
                   "--no-mount", "bind-paths,home,cwd,tmp,hostfs", "--pwd", "/",
                   "--bind", "/usr:/usr:ro", "--bind", "/lib:/lib:ro", "--bind", "/lib64:/lib64:ro",
                   artifact, "/usr/bin/cat", prefix + "/share/sai/upstream-feature-changes.json"],
                  capture_output=True, env=env)
    changes = task / "results/upstream-feature-changes.json"
    json.loads(result.stdout)
    changes.write_text(result.stdout)
    request = dict(vars(args), **allocation, artifact=str(artifact), artifact_sha256=manifest["sha256"],
                   source_sha=manifest["source_sha"], recipe_sha256=manifest["recipe_sha256"],
                   launcher=str(launcher), launcher_sha256=checksum(launcher),
                   controller_sha256=checksum(Path(__file__).resolve()),
                   baseline_module=CPU_BASELINE if not allocation["gpus_per_node"] else GPU_BASELINE,
                   feature_changes_sha256=checksum(changes), reference_evidence_sha256=reference_sha256)
    script = task / "job.sbatch"
    script.write_text(render_job(argparse.Namespace(**request)))
    script.chmod(0o700)
    wrapper = task / "rank-exec.sh"
    wrapper.write_text(RANK_WRAPPER)
    wrapper.chmod(0o700)
    call(["bash", "-n", script])
    call(["bash", "-n", wrapper])
    request["job_script_sha256"] = checksum(script)
    response = call(["sbatch", "--parsable", script], capture_output=True)
    job = response.stdout.strip().split(";")[0]
    if not job.isdigit():
        raise ValueError("invalid sbatch response")
    request["job"] = job
    (task / "job.id").write_text(job + "\n")
    (task / "request.json").write_text(json.dumps(request, sort_keys=True) + "\n")
    print(job, flush=True)


def write_manifest(path, manifest):
    temporary = path.with_name(f".{path.name}-{os.getpid()}.tmp")
    temporary.write_text(json.dumps(manifest, sort_keys=True) + "\n")
    os.replace(temporary, path)


def clear_run_proof(request):
    artifact, manifest = build_artifact(request["build_run_id"], request["version"], request["target"])
    if manifest.get(PROOF, {}).get("run_id") == request["run_id"]:
        manifest.pop(PROOF)
        write_manifest(artifact.with_suffix(".json"), manifest)


def monitor(args):
    task = run_dir(args.run_id)
    job = (task / "job.id").read_text().strip()
    if not job.isdigit():
        raise ValueError("invalid Slurm job id")
    deadline = time.monotonic() + args.timeout
    previous = None
    while time.monotonic() < deadline:
        queued = subprocess.run(["squeue", "-h", "-j", job, "-o", "%T|%R"],
                                check=False, text=True, capture_output=True).stdout.strip()
        if queued:
            if queued != previous:
                print(f"{job}: {queued}", flush=True)
                previous = queued
        else:
            rows = call(["sacct", "-X", "-n", "-P", "-j", job, "-o", "JobIDRaw,State,ExitCode"],
                        capture_output=True).stdout.splitlines()
            values = next((row.split("|") for row in rows if row.split("|")[0] == job), None)
            if values and values[1] not in ("RUNNING", "PENDING", "COMPLETING", "CONFIGURING"):
                success = values[1] == "COMPLETED" and values[2] == "0:0"
                status = {"job": job, "state": values[1], "exit_code": values[2], "verified": False}
                status_path = task / "results/status.json"
                status_path.write_text(json.dumps(status) + "\n")
                request = json.loads((task / "request.json").read_text())
                clear_run_proof(request)
                if success:
                    evidence = verify_evidence(task, request)
                    evidence_path = task / "results/evidence.json"
                    if evidence_path.is_symlink() or evidence_path.resolve() != evidence_path:
                        raise ValueError("benchmark evidence output path is untrusted")
                    evidence_path.write_text(json.dumps(evidence, sort_keys=True) + "\n")
                    artifact, manifest = build_artifact(request["build_run_id"], request["version"], request["target"])
                    manifest[PROOF] = {
                        "run_id": args.run_id, "job": job, "verified": True,
                        "partition": TARGETS[request["target"]]["partition"], **resources(request["target"]),
                        **{name: request[name] for name in ("artifact_sha256", "recipe_sha256", "source_sha",
                                                           "controller_sha256", "launcher", "launcher_sha256",
                                                           "job_script_sha256")},
                        "evidence_sha256": checksum(evidence_path),
                        "baseline_module": request["baseline_module"],
                        "feature_parity": evidence["feature_parity"],
                        "reference": evidence["reference"],
                    }
                    write_manifest(artifact.with_suffix(".json"), manifest)
                    status["verified"] = True
                status_path.write_text(json.dumps(status) + "\n")
                print("|".join(values), flush=True)
                return 0 if success else 1
        time.sleep(args.interval)
    raise TimeoutError(f"benchmark deadline reached; job {job} has not been cancelled")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="op", required=True)
    command = commands.add_parser("submit")
    command.add_argument("run_id")
    command.add_argument("version")
    command.add_argument("target", choices=sorted(BENCHMARK_TARGETS))
    command.add_argument("--build-run-id", required=True)
    command.add_argument("--reference-run-id", help="accepted GPU run supplying CPU TBLITE/DFTD4 science references")
    command.add_argument("--minutes", type=int, default=60)
    command = commands.add_parser("monitor")
    command.add_argument("run_id")
    command.add_argument("--timeout", type=int, default=7200)
    command.add_argument("--interval", type=int, default=15)
    args = parser.parse_args()
    result = {"submit": submit, "monitor": monitor}[args.op](args)
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
