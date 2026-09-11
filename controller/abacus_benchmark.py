#!/usr/bin/env python3
"""Prepare, submit, and audit paired ABACUS SCF benchmarks."""

import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import statistics
import subprocess

from remote_controller import TARGETS, safe_name
from source_cache import checksum

ROOT = Path(os.environ.get("SAI_SOFTWARE_ROOT", Path.home() / "sai-hpc-software")).resolve()
CONTROL = Path(__file__).resolve()
SYSTEM_MODULE = "abacus/v3.9.0.26-sm70-auto"
SYSTEM_MODULES = (SYSTEM_MODULE, "abacus/develop-git-079fd0c-260724-sm70-auto")
MPI_MODULE = "openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto"


def task_dir(run):
    path = ROOT / "runtime-tests" / ("benchmark-" + safe_name(run))
    if path.resolve() != path:
        raise ValueError("benchmark directory must not contain symlinks")
    return path


def regular(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path or not path.is_file():
        raise ValueError(f"expected a regular absolute file without symlinks: {path}")
    return path


def dump(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")


def case_files(case):
    if not case.is_dir() or case.resolve() != case:
        raise ValueError("case must be an absolute materialized directory")
    files = {}
    for path in sorted(case.rglob("*")):
        if path.is_symlink() or (not path.is_file() and not path.is_dir()):
            raise ValueError("case must contain only materialized regular files")
        if any(part.startswith("OUT.") for part in path.relative_to(case).parts):
            raise ValueError("case contains previous calculation outputs")
        if path.is_file():
            files[str(path.relative_to(case))] = checksum(path)
    if not {"INPUT", "STRU"} <= files.keys() or len(files) > 256:
        raise ValueError("small case requires INPUT and STRU and at most 256 files")
    if sum((case / name).stat().st_size for name in files) > 64 * 1024**2:
        raise ValueError("materialized small case exceeds 64 MiB")
    for name in ("INPUT", "STRU"):
        for line in (case / name).read_text().splitlines():
            for token in line.split("#", 1)[0].split():
                if token.startswith("/") or ".." in Path(token).parts:
                    raise ValueError("case references must remain inside the materialized directory")
    return files


def sequence(warmup, repeats):
    if not 1 <= warmup <= 10 or not 3 <= repeats <= 30:
        raise ValueError("require 1..10 warmup pairs and 3..30 measured pairs")
    runs = []
    for pair in range(warmup + repeats):
        measured = pair >= warmup
        index = pair - warmup if measured else pair
        for arm in (("system", "candidate") if pair % 2 == 0 else ("candidate", "system")):
            runs.append({"id": f"{'m' if measured else 'w'}{index:03d}-{arm}",
                         "arm": arm, "measured": measured, "pair": pair})
    return runs


def render_job(r, task):
    q = lambda value: shlex.quote(str(value))
    target = TARGETS[r["target"]]
    cpu = target["gpus"] == 0
    resource = (["#SBATCH --cpus-per-task=2"] if cpu else ["#SBATCH --gpus-per-node=1"])
    mapping = (f"export MAP_OPT=ppr:8:node:pe=2" if cpu else
               f"source /opt/sai_config/mps_mapping.d/{target['partition']}.bash")
    trace = ('printf "%s\\t%s\\t%s\\t%s\\t%s\\t%s\\n" "$(hostname)" '
             '"$OMPI_COMM_WORLD_RANK" "$BENCH_TARGET" "$1" "${CUDA_VISIBLE_DEVICES:-}" '
             '"${OPAL_PREFIX:-}" > "$SAI_ABACUS_TRACE_DIR/rank-$OMPI_COMM_WORLD_RANK.tsv"; exec "$@"')
    lines = ["#!/usr/bin/env bash", f"#SBATCH --job-name=bench-abacus-{r['run_id']}",
             f"#SBATCH --partition={target['partition']}", f"#SBATCH --qos={target['qos']}",
             "#SBATCH --nodes=2", f"#SBATCH --ntasks={r['ranks']}",
             f"#SBATCH --ntasks-per-node={r['ranks_per_node']}", *resource,
             f"#SBATCH --time={r['minutes']}", f"#SBATCH --output={task}/slurm-%j.log",
             "#SBATCH --export=NIL", "set -euo pipefail", f"export HOME={q(Path.home())}",
             "export USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?}",
             "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
             'export LD_LIBRARY_PATH="" LD_PRELOAD=""',
             "unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH",
             f"export SAI_SOFTWARE_ROOT={q(ROOT)} SAI_ABACUS_VERSION={q(r['version'])}",
             f"cd {q(task)}", f"python3 {q(CONTROL)} _verify {q(r['run_id'])}",
             'test "${SLURM_JOB_NUM_NODES:?}" = 2',
             f'test "${{SLURM_JOB_PARTITION:?}}" = {q(target["partition"])}',
             'test ! -e execution.id', 'printf "%s\\n" "$SLURM_JOB_ID" > execution.id',
             f"export SAI_ABACUS_IMAGE={q(r['artifact'])} BENCH_TARGET={q(r['target'])}",
             f"export TMPDIR={q(task / 'mpi-runtime')}", 'mkdir -p "$TMPDIR"',
             "export SLURM_EXPORT_ENV=ALL OMPI_MCA_plm_slurm_args=--external-launcher",
             "export PRTE_MCA_plm_slurm_args=--external-launcher",
             "run_one() (", "set -euo pipefail", 'arm=$1; id=$2',
             'work="$PWD/runs/$id"', 'mkdir -p "$work/ranks"', 'cp -a input/. "$work/"',
             'export SAI_ABACUS_TRACE_DIR="$work/ranks"', 'cd "$work"',
             "source /etc/profile.d/lmod.sh", "module purge",
             "module use /opt/modules/modulefiles/apps",
             "module use /opt/modules/modulefiles/devtools",
             f'if [[ "$arm" == system ]]; then module load {q(r["system_module"])}; fi',
             f"module load apptainer/1.4.4 {MPI_MODULE}", mapping,
             f"export OMP_NUM_THREADS={r['threads']}",
             f'[[ "${{OPAL_PREFIX:?}}" == *-{r["dependency_isa"]} ]]',
             'module -t list > modules.log 2>&1',
             'if [[ "$arm" == system ]]; then', '  executable=$(command -v abacus)',
             '  executable=$(realpath -e -- "$executable")',
             '  "$executable" --info > info.log 2>&1', '  ldd "$executable" > ldd.log 2>&1',
             '  sha256sum "$executable" > executable.sha256',
             '  printf "%s\\n" "$executable" > executable.path',
             f'  command=(bash -c {q(trace)} bash "$executable")', "else",
             f"  executable={q(r['launcher'])}", '  command=("$executable")', "fi",
             'sha256sum "$SAI_ABACUS_IMAGE" > artifact.sha256',
             f"sha256sum {q(r['launcher'])} > launcher.sha256",
             f'read -r actual _ < artifact.sha256; [[ "$actual" == {r["artifact_sha256"]} ]]',
             f'read -r actual _ < launcher.sha256; [[ "$actual" == {r["launcher_sha256"]} ]]',
             f'/usr/bin/time -f %e -o wall-seconds.txt mpirun -np {r["ranks"]} '
             '--map-by "$MAP_OPT" --report-bindings "${command[@]}" > stdout.log 2>&1',
             'printf "%s\\n" "$SLURM_JOB_ID" > completed.job', ")"]
    lines += [f"run_one {s['arm']} {s['id']}" for s in r["runs"]]
    lines += [f"python3 {q(CONTROL)} analyze {q(r['run_id'])}"]
    return "\n".join(lines) + "\n"


def prepare(args):
    task = task_dir(args.run_id)
    safe_name(args.version)
    if task.exists() or args.minutes < 1 or args.system_module not in SYSTEM_MODULES:
        raise ValueError("choose a fresh run, positive wall limit, and the exact system module")
    artifact, launcher = regular(args.artifact), regular(args.launcher)
    catalog = ROOT / "containers/software/abacus" / args.version / args.target
    if artifact.parent != catalog or artifact.suffix != ".sif" or artifact.name == "current.sif":
        raise ValueError("artifact must be a pinned SIF in the version/target catalog")
    if not launcher.is_relative_to(ROOT / "controller") or not os.access(launcher, os.X_OK):
        raise ValueError("launcher must be an executable trusted controller file")
    case = Path(args.case)
    files = case_files(case)
    target = TARGETS[args.target]
    cpu = target["gpus"] == 0
    inputs = dict(re.findall(r"^[ \t]*(\w+)[ \t]+(\S+)", (case / "INPUT").read_text(), re.M))
    device = inputs.get("device", "cpu")
    allow_cpu = getattr(args, "allow_cpu_case_on_gpu", False)
    if (inputs.get("calculation", "scf") != "scf" or device not in {"cpu", "gpu"} or
            (cpu and device != "cpu") or (not cpu and device == "cpu" and not allow_cpu)):
        raise ValueError("case must request SCF and the target's CPU/GPU device")
    scf = Path(args.scf_log)
    reserved = {"ranks", "stdout.log", "wall-seconds.txt", "completed.job", "modules.log",
                "artifact.sha256", "launcher.sha256", "info.log", "ldd.log",
                "executable.sha256", "executable.path"}
    if scf.is_absolute() or ".." in scf.parts or (case / scf).exists():
        raise ValueError("SCF log must be a fresh relative output, absent from case inputs")
    if any((case / name).exists() for name in reserved):
        raise ValueError("case contains reserved benchmark evidence paths")
    r = dict(vars(args), artifact=str(artifact), launcher=str(launcher),
             artifact_sha256=checksum(artifact), launcher_sha256=checksum(launcher),
             controller_sha256=checksum(CONTROL), case_files=files, case_device=device,
             allow_cpu_case_on_gpu=allow_cpu,
             ranks_per_node=8 if cpu else 1, ranks=16 if cpu else 2, threads=2 if cpu else 1,
             dependency_isa=target.get("dependency_isa", "avx512" if args.target in
                                       {"dsprhbm", "4v100-avx512"} else "avx2"),
             runs=sequence(args.warmup, args.repeats), schema=1)
    task.mkdir(parents=True)
    shutil.copytree(case, task / "input")
    if case_files(task / "input") != files:
        raise ValueError("case changed while being materialized")
    script = task / "job.sbatch"
    script.write_text(render_job(r, task))
    subprocess.run(["bash", "-n", str(script)], check=True)
    r["job_script_sha256"] = checksum(script)
    dump(task / "request.json", r)
    (task / "request.sha256").write_text(checksum(task / "request.json") + "\n")
    return task


def verify(task):
    r = json.loads(regular(task / "request.json").read_text())
    if checksum(task / "request.json") != regular(task / "request.sha256").read_text().strip():
        raise ValueError("request checksum mismatch")
    for path, key in ((Path(r["artifact"]), "artifact_sha256"),
                      (Path(r["launcher"]), "launcher_sha256"),
                      (CONTROL, "controller_sha256"), (task / "job.sbatch", "job_script_sha256")):
        if checksum(regular(path)) != r[key]:
            raise ValueError(f"checksum mismatch: {key}")
    if case_files(task / "input") != r["case_files"] or r["runs"] != sequence(r["warmup"], r["repeats"]):
        raise ValueError("case or expected run sequence changed")
    return r


def parse_scf(stdout, scf, wall):
    if "#SCF IS CONVERGED#" not in scf:
        raise ValueError("SCF did not converge")
    energies = re.findall(r"!FINAL_ETOT_IS\s+(\S+)\s+eV", scf)
    if not energies:
        raise ValueError("final SCF energy missing")
    times, in_table = [], False
    for line in stdout.splitlines():
        fields = line.split()
        if fields[:2] == ["ITER", "ETOT/eV"] and fields[-1:] == ["TIME/s"]:
            in_table = True
        elif in_table and fields and re.fullmatch(r"[A-Za-z]+\d+", fields[0]):
            if len(fields) != 5 or not all(math.isfinite(float(x)) for x in fields[1:]):
                raise ValueError("malformed or nonfinite SCF iteration")
            times.append(float(fields[-1]))
    energy, wall_seconds = float(energies[-1]), float(wall.strip())
    if (not times or any(t < 0 for t in times) or
            not all(math.isfinite(float(e)) for e in energies) or
            not all(math.isfinite(x) for x in (energy, wall_seconds)) or
            wall_seconds <= 0 or sum(times) <= 0):
        raise ValueError("missing or invalid finite positive SCF timing/energy")
    return {"energy_ev": energy, "wall_seconds": wall_seconds,
            "rounded_iteration_seconds": round(sum(times), 8), "iterations": len(times)}


def analyze(task):
    # Re-analysis must not leave a previous success report after new evidence fails.
    for name in ("evidence.json", "evidence.sha256"):
        if (task / name).exists():
            regular(task / name).unlink()
    r = verify(task)
    files, records, binaries = {}, [], set()

    def read(relative):
        path = regular(task / relative)
        files[str(relative)] = checksum(path)
        return path.read_text()

    job = read("execution.id").strip()
    if not job.isdigit() or read("job.id").strip() != job:
        raise ValueError("results are not from the submitted allocation")
    expected = {s["id"] for s in r["runs"]}
    if {p.name for p in (task / "runs").iterdir()} != expected:
        raise ValueError("missing or unexpected benchmark runs")
    for spec in r["runs"]:
        base = Path("runs") / spec["id"]
        if read(base / "completed.job").strip() != job:
            raise ValueError("run incomplete or allocation changed")
        for name in ("artifact", "launcher"):
            if read(base / (name + ".sha256")).split()[0] != r[name + "_sha256"]:
                raise ValueError(f"run {name} checksum mismatch")
        for name, sha in r["case_files"].items():
            files[str(base / name)] = checksum(regular(task / base / name))
            if files[str(base / name)] != sha:
                raise ValueError("run input differs from the shared case")
        modules = set(read(base / "modules.log").split())
        if MPI_MODULE not in modules or (spec["arm"] == "system" and r["system_module"] not in modules):
            raise ValueError("required exact module evidence is missing")
        executable = r["artifact"]
        if spec["arm"] == "system":
            executable = read(base / "executable.path").strip()
            binaries.add((executable, read(base / "executable.sha256").split()[0]))
            info, loader = read(base / "info.log"), read(base / "ldd.log")
            if not info.strip() or not loader.strip() or re.search(r"\bnot found\b", loader):
                raise ValueError("system version/loader evidence is missing")
        hosts = Counter()
        rank_dir = task / base / "ranks"
        if {p.name for p in rank_dir.iterdir()} != {f"rank-{i}.tsv" for i in range(r["ranks"])}:
            raise ValueError("incomplete rank traces")
        for rank in range(r["ranks"]):
            row = read(base / f"ranks/rank-{rank}.tsv").rstrip("\n").split("\t")
            if (len(row) != 6 or row[1:4] != [str(rank), r["target"], executable] or
                    not row[0] or not row[5].endswith("-" + r["dependency_isa"]) or
                    (TARGETS[r["target"]]["gpus"] and not row[4])):
                raise ValueError("rank trace does not match target/image/MPI")
            hosts[row[0]] += 1
        if len(hosts) != 2 or set(hosts.values()) != {r["ranks_per_node"]}:
            raise ValueError("ranks did not execute on both nodes")
        result = parse_scf(read(base / "stdout.log"), read(base / r["scf_log"]),
                           read(base / "wall-seconds.txt"))
        records.append(dict(spec, **result, nodes=dict(hosts)))
    if len(binaries) != 1 or len({tuple(sorted(x["nodes"])) for x in records}) != 1:
        raise ValueError("system binary or allocation nodes changed")
    delta = max(x["energy_ev"] for x in records) - min(x["energy_ev"] for x in records)
    if delta > 1e-5:
        raise ValueError("SCF energy difference exceeds 1e-5 eV")
    stats = {}
    for metric in ("wall_seconds", "rounded_iteration_seconds"):
        stats[metric] = {}
        for arm in ("system", "candidate"):
            values = [x[metric] for x in records if x["measured"] and x["arm"] == arm]
            stats[metric][arm] = dict(median=statistics.median(values), min=min(values), max=max(values))
        stats[metric]["candidate_system_ratio"] = stats[metric]["candidate"]["median"] / stats[metric]["system"]["median"]
    evidence = dict(schema=1, job=job, request_sha256=checksum(task / "request.json"),
                    system_module=r["system_module"], case_device=r["case_device"],
                    artifact_sha256=r["artifact_sha256"], launcher_sha256=r["launcher_sha256"],
                    energy_spread_ev=delta, statistics=stats, runs=records, files=files,
                    timing_note="Iteration times sum the rounded SCF TIME/s column; wall measures mpirun only.")
    dump(task / "evidence.json", evidence)
    (task / "evidence.sha256").write_text(checksum(task / "evidence.json") + "\n")
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("prepare", "render"):
        command = commands.add_parser(action, help="materialize inputs and script; do not submit")
        command.add_argument("run_id")
        command.add_argument("version")
        command.add_argument("target", choices=TARGETS)
        for name in ("artifact", "launcher", "system-module", "case"):
            command.add_argument("--" + name, required=True)
        command.add_argument("--warmup", type=int, default=1)
        command.add_argument("--repeats", type=int, default=3)
        command.add_argument("--minutes", type=int, default=60)
        command.add_argument("--scf-log", default="OUT.autotest/running_scf.log")
        command.add_argument("--allow-cpu-case-on-gpu", action="store_true",
                             help="explicitly compare a CPU case using the GPU allocation's rank layout")
    for action in ("submit", "analyze", "_verify"):
        commands.add_parser(action).add_argument("run_id")
    args = parser.parse_args()
    try:
        if args.action in {"prepare", "render"}:
            print(prepare(args))
        else:
            task = task_dir(args.run_id)
            if args.action == "submit":
                verify(task)
                if (task / "job.id").exists() or (task / "execution.id").exists():
                    raise ValueError("benchmark already submitted")
                result = subprocess.run(["sbatch", "--parsable", str(task / "job.sbatch")],
                                        check=True, capture_output=True, text=True)
                job = result.stdout.strip().split(";")[0]
                if not job.isdigit():
                    raise ValueError("invalid sbatch response")
                (task / "job.id").write_text(job + "\n")
                print(job)
            elif args.action == "analyze":
                print(json.dumps(analyze(task)["statistics"], sort_keys=True))
            else:
                verify(task)
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"benchmark: {error}\n")


if __name__ == "__main__":
    main()
