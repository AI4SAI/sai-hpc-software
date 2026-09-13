#!/usr/bin/env python3
"""Submit and monitor host-MPI, multi-node tests of pinned SIF candidates."""
import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import time

from remote_controller import TARGETS, safe_name
from source_cache import checksum
from release_contract import validate_identity
from delivery_layout import CONTRACT_SCHEMA, artifact_path, load_artifact, validate_record, validate_runtime_identity

ROOT = Path(os.environ.get("SAI_SOFTWARE_ROOT", Path.home() / "sai-hpc-software")).resolve()
CONTROL = Path(__file__).resolve().parent
MAPPING_ROOT = Path("/opt/sai_config/mps_mapping.d")
RUNTIME_TARGETS = {"dsprhbm", "4v100-avx512", "16v100-avx2", "8v100v0-avx512"}


def call(argv, **kwargs):
    return subprocess.run([str(value) for value in argv], check=True, text=True, **kwargs)


def run_dir(run_id):
    path = ROOT / "runtime-tests" / safe_name(run_id)
    if path.resolve() != path:
        raise ValueError("runtime-test path must not be a symlink")
    return path


def build_artifact(build_run_id, version, target):
    """Resolve the exact artifact produced by one completed build run."""
    build_run_id = safe_name(build_run_id)
    build_task = ROOT / "runs" / build_run_id
    if build_task.resolve() != build_task:
        raise ValueError("untrusted build run path")
    request_path = build_task / "request.json"
    if request_path.is_symlink():
        raise ValueError("untrusted build request")
    request = json.loads(request_path.read_text())
    if request.get("contract_schema") != CONTRACT_SCHEMA:
        raise ValueError("legacy build requests cannot receive new delivery acceptance")
    identity = validate_record(request)
    if identity["software"] != "abacus" or identity["target"] != target or identity["source_version"] != version:
        raise ValueError("requested runtime differs from the build identity")
    artifact_file = build_task / "artifact.path"
    if not artifact_file.is_file() or artifact_file.is_symlink():
        raise ValueError("build run has no trusted artifact reference")
    artifact = Path(artifact_file.read_text().strip())
    expected = artifact_path(ROOT, identity, build_run_id)
    if (artifact != expected or artifact.resolve() != expected or
            not artifact.is_file() or artifact.is_symlink()):
        raise ValueError("build artifact does not match the requested run")
    manifest = load_artifact(ROOT, artifact, software="abacus", target=target)
    if manifest["identity"] != identity:
        raise ValueError("build artifact identity differs from its request")
    return artifact


def runtime_resources(args):
    """Resolve target-aware defaults and enforce the allocation's QOS budget."""
    if args.target not in RUNTIME_TARGETS:
        raise ValueError("multi-node runtime acceptance is not registered for this target")
    cpu_only = TARGETS[args.target]["gpus"] == 0
    gpus_per_node = getattr(args, "gpus_per_node", None)
    if gpus_per_node is None:
        gpus_per_node = 0 if cpu_only else 1
    ranks_per_node = getattr(args, "ranks_per_node", 8) if cpu_only else gpus_per_node
    cpus_per_task = getattr(args, "cpus_per_task", 2) if cpu_only else 1
    if args.nodes != 2 or gpus_per_node != (0 if cpu_only else 1):
        raise ValueError("runtime resources outside acceptance bounds")
    if cpu_only and (ranks_per_node < 1 or cpus_per_task < 1 or
                     ranks_per_node * cpus_per_task > 16):
        raise ValueError("runtime resources outside the QOS CPU budget")
    return {"gpus_per_node": gpus_per_node, "ranks_per_node": ranks_per_node,
            "cpus_per_task": cpus_per_task, "ranks": args.nodes * ranks_per_node}


def render_job(args):
    identity = validate_identity(args.identity)
    if identity["source_version"] != args.version or identity["target"] != args.target or identity["software"] != "abacus":
        raise ValueError("runtime arguments differ from identity")
    target = TARGETS[args.target]
    task = run_dir(args.run_id)
    artifact = Path(args.artifact)
    if artifact != artifact_path(ROOT, identity, artifact.stem):
        raise ValueError("runtime artifact is outside the identity catalog")
    launcher = Path(getattr(args, "launcher", CONTROL / "abacus")).resolve()
    prefix = identity["install_prefix"]
    cpu_only = target["gpus"] == 0
    resources = runtime_resources(args)
    ranks_per_node = resources["ranks_per_node"]
    cpus_per_task = resources["cpus_per_task"]
    ranks = resources["ranks"]
    q = shlex.quote
    results = task / "results"
    case = task / "case"
    runtime = task / "apptainer-runtime"
    mpi_runtime = task / "mpi-runtime"
    mapping = MAPPING_ROOT / (target["partition"] + ".bash")
    mpi_isa = target["dependency_isa"]
    resource_lines = (
        [f"#SBATCH --ntasks-per-node={ranks_per_node}",
         f"#SBATCH --cpus-per-task={cpus_per_task}"]
        if cpu_only else
        [f"#SBATCH --ntasks-per-node={resources['gpus_per_node']}",
         f"#SBATCH --gpus-per-node={resources['gpus_per_node']}"])
    mapping_lines = (
        [f"export MAP_OPT={q('ppr:%d:node:pe=%d' % (ranks_per_node, cpus_per_task))}",
         f"export OMP_NUM_THREADS={q(str(cpus_per_task))}"]
        if cpu_only else
        [f"source {q(str(mapping))}"])
    device_lines = (
        [f"sed -i {q('s/^device[[:space:]]*gpu/device            cpu/')} "
         f"{q(str(case / 'INPUT'))}"]
        if cpu_only else [])
    hardware_lines = (["nvidia-smi -L"] if not cpu_only else [])
    gpu_check_lines = (
        [f"grep -Eq 'GPU.*\\(x{ranks}\\)' {q(str(case / 'OUT.autotest/running_scf.log'))}"]
        if not cpu_only else [])
    trace_check = ('NF != 6 || $6 == ""' if cpu_only else
                   'NF != 6 || $5 == "" || $6 == ""')
    lines = [
        "#!/usr/bin/env bash",
        f"#SBATCH --job-name=runtime-abacus-{args.run_id}",
        f"#SBATCH --partition={target['partition']}",
        f"#SBATCH --qos={target['qos']}",
        f"#SBATCH --nodes={args.nodes}",
        f"#SBATCH --ntasks={ranks}",
        *resource_lines,
        f"#SBATCH --time={args.minutes}",
        f"#SBATCH --output={results}/slurm-%j.log",
        "#SBATCH --export=NIL",
        "set -euo pipefail",
        f"export HOME={q(str(Path.home()))}",
        "export USER=${SLURM_JOB_USER:?}",
        "export LOGNAME=$USER",
        "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        'export LD_LIBRARY_PATH="" LD_PRELOAD=""',
        f"export TMPDIR={q(str(mpi_runtime))} APPTAINER_TMPDIR={q(str(runtime))}",
        f"export APPTAINER_CACHEDIR={q(str(task / 'apptainer-cache'))}",
        "unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH",
        "source /etc/profile.d/lmod.sh",
        "module purge",
        "module use /opt/modules/modulefiles/devtools",
        "module load apptainer/1.4.4 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto",
        f"export SAI_SOFTWARE_ROOT={q(str(ROOT))}",
        f"export SAI_ABACUS_VERSION={q(args.version)}",
        "command -v apptainer >/dev/null",
        f"cd {q(str(task))}",
        *mapping_lines,
        "export SLURM_EXPORT_ENV=ALL",
        "export OMPI_MCA_plm_slurm_args=--external-launcher",
        "export PRTE_MCA_plm_slurm_args=--external-launcher",
        f"image={q(str(artifact))}",
        f"launcher={q(str(launcher))}",
        'test -r "$image"',
        'test -x "$launcher"',
        f"mkdir -p {q(str(results / 'ranks'))} {q(str(case))} {q(str(runtime))} {q(str(mpi_runtime))}",
        "apptainer exec --cleanenv --no-home "
        "--no-mount bind-paths,home,cwd,tmp,hostfs --pwd /work "
        "--bind /usr:/usr:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro "
        f"--bind {q(str(case))}:/work:rw \"$image\" /usr/bin/cp -a "
        f"{q(prefix + '/share/sai/smoke-case/.')} /work/",
        *device_lines,
        f"cd {q(str(case))}",
        f"export SAI_ABACUS_TRACE_DIR={q(str(results / 'ranks'))}",
        'export SAI_ABACUS_IMAGE="$image"',
        *hardware_lines,
        "command -v mpirun apptainer",
        f"mpirun -np {ranks} --map-by \"$MAP_OPT\" --report-bindings \"$launcher\" > {q(str(results / 'abacus.log'))} 2>&1",
        f"test \"$(find {q(str(results / 'ranks'))} -maxdepth 1 -name 'rank-*.tsv' -type f | wc -l)\" -eq {ranks}",
        f"test \"$(cut -f1 {q(str(results / 'ranks'))}/rank-*.tsv | sort -u | wc -l)\" -eq {args.nodes}",
        f"test \"$(cut -f3 {q(str(results / 'ranks'))}/rank-*.tsv | sort -u)\" = {q(args.target)}",
        f"test \"$(cut -f4 {q(str(results / 'ranks'))}/rank-*.tsv | sort -u)\" = \"$image\"",
        f"awk -F '\\t' '{trace_check} {{ exit 1 }}' {q(str(results / 'ranks'))}/rank-*.tsv",
        f"awk -F '\\t' '$6 !~ /-{mpi_isa}$/ {{ exit 1 }}' {q(str(results / 'ranks'))}/rank-*.tsv",
        f"grep -q '#SCF IS CONVERGED#' {q(str(case / 'OUT.autotest/running_scf.log'))}",
        *gpu_check_lines,
        f"actual=$(awk '/!FINAL_ETOT_IS/{{value=$2}} END{{print value}}' {q(str(case / 'OUT.autotest/running_scf.log'))})",
        "awk -v actual=\"$actual\" -v expected=-4869.7470519303351466 "
        "'BEGIN { if (actual !~ /^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?$/) exit 1; "
        "delta=actual-expected; if (delta<0) delta=-delta; exit !(delta<=1e-5) }'",
        f"printf '%s\\n' \"$actual\" > {q(str(results / 'final-energy-ev.txt'))}",
        f"cat {q(str(results / 'ranks'))}/rank-*.tsv",
        "echo MULTINODE_CONTAINER_MPI_VERIFIED",
    ]
    return "\n".join(lines) + "\n"


def submit(args):
    resources = runtime_resources(args)
    for name, value in resources.items():
        setattr(args, name, value)
    task = run_dir(args.run_id)
    if task.exists():
        raise ValueError("runtime test already exists; choose a fresh run id")
    for name in ("results", "apptainer-runtime", "apptainer-cache", "mpi-runtime"):
        (task / name).mkdir(parents=True, exist_ok=True)
    artifact = build_artifact(args.build_run_id, args.version, args.target)
    launcher = CONTROL / "abacus"
    if not launcher.is_file() or launcher.is_symlink() or not os.access(launcher, os.X_OK):
        raise ValueError("trusted runtime launcher is missing")
    args.artifact = str(artifact)
    args.identity = load_artifact(ROOT, artifact, software="abacus", target=args.target)["identity"]
    args.launcher = str(launcher)
    script = task / "job.sbatch"
    script.write_text(render_job(args))
    script.chmod(0o700)
    call(["bash", "-n", script])
    script_sha256 = checksum(script)
    result = call(["sbatch", "--parsable", script], capture_output=True)
    job = result.stdout.strip().split(";")[0]
    if not job.isdigit():
        raise ValueError("invalid sbatch response")
    (task / "job.id").write_text(job + "\n")
    request = dict(vars(args), artifact=str(artifact), artifact_sha256=checksum(artifact),
                   launcher=str(launcher), launcher_sha256=checksum(launcher),
                   controller_sha256=checksum(Path(__file__).resolve()),
                   job_script_sha256=script_sha256)
    (task / "request.json").write_text(json.dumps(request, sort_keys=True) + "\n")
    print(job, flush=True)


def verify_evidence(task, request):
    """Recheck raw scientific results; a zero Slurm exit is not acceptance."""
    task = Path(task)
    identity = validate_runtime_identity(ROOT, request)
    files = {}

    def read(relative):
        path = task / relative
        if not path.is_file() or path.is_symlink() or path.resolve() != path:
            raise ValueError(f"runtime evidence is missing or untrusted: {relative}")
        files[str(relative)] = checksum(path)
        return path.read_text()

    read("job.sbatch")
    if files["job.sbatch"] != request["job_script_sha256"]:
        raise ValueError("runtime job script changed")
    job = read("job.id").strip()
    if not job.isdigit():
        raise ValueError("invalid runtime evidence job id")
    resources = runtime_resources(argparse.Namespace(**request))
    for name, value in resources.items():
        if request.get(name) != value:
            raise ValueError("runtime request resources are inconsistent")
    ranks = resources["ranks"]
    trace_dir = task / "results/ranks"
    expected_files = {f"rank-{rank}.tsv" for rank in range(ranks)}
    if {path.name for path in trace_dir.glob("rank-*.tsv")} != expected_files:
        raise ValueError("runtime rank files do not match the requested ranks")
    mpi_isa = TARGETS[request["target"]]["dependency_isa"]
    hosts = Counter()
    for rank in range(ranks):
        lines = read(f"results/ranks/rank-{rank}.tsv").splitlines()
        if len(lines) != 1 or len(lines[0].split("\t")) != 6:
            raise ValueError("runtime rank trace is malformed")
        host, actual_rank, target, image, gpu, mpi = lines[0].split("\t")
        if (not host or actual_rank != str(rank) or target != request["target"] or
                image != request["artifact"] or not mpi.startswith("/") or
                not mpi.endswith(f"-{mpi_isa}") or
                (resources["gpus_per_node"] and not gpu)):
            raise ValueError("runtime rank trace does not match the pinned allocation")
        hosts[host] += 1
    if (len(hosts) != request["nodes"] or
            any(count != resources["ranks_per_node"] for count in hosts.values())):
        raise ValueError("runtime ranks are not distributed across the requested nodes")
    slurm_log = read(f"results/slurm-{job}.log")
    if "MULTINODE_CONTAINER_MPI_VERIFIED" not in slurm_log.splitlines():
        raise ValueError("runtime success marker is missing")
    read("results/abacus.log")
    scf = read("case/OUT.autotest/running_scf.log")
    if "#SCF IS CONVERGED#" not in scf:
        raise ValueError("runtime SCF did not converge")
    if resources["gpus_per_node"] and not re.search(rf"GPU.*\(x{ranks}\)", scf):
        raise ValueError("runtime SCF did not use all requested GPUs")
    energies = re.findall(r"^\s*!FINAL_ETOT_IS\s+(\S+)", scf, re.MULTILINE)
    try:
        energy = float(energies[-1]) if energies else math.nan
        recorded_energy = float(read("results/final-energy-ev.txt").strip())
    except ValueError as error:
        raise ValueError("runtime final energy is invalid") from error
    if (not math.isfinite(energy) or abs(energy - (-4869.7470519303351466)) > 1e-5 or
            not math.isfinite(recorded_energy) or energy != recorded_energy):
        raise ValueError("runtime final energy failed the scientific tolerance")
    return {"schema": 1, "identity": identity, "job": job, "ranks": ranks, "nodes": dict(sorted(hosts.items())),
            "final_energy_ev": energy, "artifact_sha256": request["artifact_sha256"],
            "controller_sha256": request["controller_sha256"],
            "job_script_sha256": request["job_script_sha256"], "files": files}


def clear_run_proof(task, run_id):
    """Invalidate only this runtime run's previous proof, never another run's."""
    request_path = task / "request.json"
    if not request_path.is_file():
        return
    request = json.loads(request_path.read_text())
    artifact = Path(request["artifact"])
    expected = artifact_path(ROOT, request["identity"], request["build_run_id"])
    if artifact != expected or artifact.resolve() != expected:
        raise ValueError("runtime artifact reference changed")
    sidecar = artifact.with_suffix(".json")
    if not sidecar.is_file() or sidecar.is_symlink():
        raise ValueError("runtime artifact manifest is missing")
    manifest = json.loads(sidecar.read_text())
    if validate_record(manifest) != validate_identity(request["identity"]):
        raise ValueError("runtime proof cannot modify another delivery identity")
    if manifest.get("multinode_runtime", {}).get("run_id") == run_id:
        manifest.pop("multinode_runtime")
        temporary = sidecar.with_name(f".{sidecar.name}-{os.getpid()}.tmp")
        temporary.write_text(json.dumps(manifest, sort_keys=True) + "\n")
        os.replace(temporary, sidecar)


def monitor(args):
    task = run_dir(args.run_id)
    job = (task / "job.id").read_text().strip()
    if not job.isdigit():
        raise ValueError("invalid job id")
    deadline = time.monotonic() + args.timeout
    previous = None
    while time.monotonic() < deadline:
        queue_result = subprocess.run(
            ["squeue", "-h", "-j", job, "-o", "%T|%R"],
            check=False, text=True, capture_output=True)
        # Slurm returns exit 1 for a completed job that has already left
        # squeue; sacct below is the authoritative terminal-state source.
        queued = queue_result.stdout.strip()
        if queued:
            if queued != previous:
                print(f"{job}: {queued}", flush=True)
                previous = queued
        else:
            rows = call(["sacct", "-X", "-n", "-P", "-j", job,
                         "-o", "JobIDRaw,State,ExitCode"], capture_output=True).stdout.splitlines()
            values = next((row.split("|") for row in rows if row.split("|")[0] == job), None)
            if values and values[1] not in ("RUNNING", "PENDING", "COMPLETING"):
                success = values[1] == "COMPLETED" and values[2] == "0:0"
                status = {"job": job, "state": values[1], "exit_code": values[2],
                          "verified": False}
                (task / "results/status.json").write_text(json.dumps(status) + "\n")
                # A rerun of monitor must not leave a stale proof if validation
                # now fails or Slurm reports that this run failed.
                clear_run_proof(task, args.run_id)
                if success:
                    try:
                        request = json.loads((task / "request.json").read_text())
                        artifact = Path(request["artifact"])
                        if (not artifact.is_file() or artifact.is_symlink() or
                                checksum(artifact) != request["artifact_sha256"]):
                            raise ValueError("runtime-tested artifact changed")
                        launcher = Path(request["launcher"])
                        if (not launcher.is_file() or launcher.is_symlink() or
                                checksum(launcher) != request["launcher_sha256"]):
                            raise ValueError("runtime launcher changed")
                        if checksum(Path(__file__).resolve()) != request["controller_sha256"]:
                            raise ValueError("runtime acceptance controller changed")
                        evidence = verify_evidence(task, request)
                        evidence_path = task / "results/evidence.json"
                        if evidence_path.is_symlink() or evidence_path.resolve() != evidence_path:
                            raise ValueError("runtime evidence output path is untrusted")
                        evidence_path.write_text(json.dumps(evidence, sort_keys=True) + "\n")
                        sidecar = artifact.with_suffix(".json")
                        manifest = json.loads(sidecar.read_text())
                        verification = {
                            "run_id": args.run_id, "job": job, "identity": request["identity"],
                            "partition": TARGETS[request["target"]]["partition"],
                            "nodes": request["nodes"],
                            **runtime_resources(argparse.Namespace(**request)),
                            "artifact_sha256": request["artifact_sha256"],
                            "controller_sha256": request["controller_sha256"],
                            "job_script_sha256": request["job_script_sha256"],
                            "evidence_sha256": checksum(evidence_path),
                            "launcher": str(launcher),
                            "launcher_sha256": request["launcher_sha256"],
                            "verified": True,
                        }
                        manifest["multinode_runtime"] = verification
                        sidecar_tmp = sidecar.with_name(f".{sidecar.name}-{os.getpid()}.tmp")
                        sidecar_tmp.write_text(json.dumps(manifest, sort_keys=True) + "\n")
                        os.replace(sidecar_tmp, sidecar)
                        status["verified"] = True
                    except Exception:
                        (task / "results/status.json").write_text(json.dumps(status) + "\n")
                        raise
                (task / "results/status.json").write_text(json.dumps(status) + "\n")
                print("|".join(values), flush=True)
                log = task / "results" / f"slurm-{job}.log"
                if log.is_file():
                    print("\n".join(log.read_text(errors="replace").splitlines()[-100:]), flush=True)
                return 0 if success else 1
        time.sleep(args.interval)
    raise TimeoutError(f"monitor deadline reached; job {job} was not cancelled")


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="op", required=True)
    command = commands.add_parser("submit")
    for field in ("run_id", "version"):
        command.add_argument(field)
    command.add_argument("target", choices=sorted(RUNTIME_TARGETS))
    command.add_argument("--build-run-id", required=True)
    command.add_argument("--nodes", type=int, default=2)
    command.add_argument("--gpus-per-node", type=int, default=None,
                         help="default: 0 for CPU targets, 1 for GPU targets")
    command.add_argument("--ranks-per-node", type=int, default=8)
    command.add_argument("--cpus-per-task", type=int, default=2)
    command.add_argument("--minutes", type=int, default=30)
    command = commands.add_parser("monitor")
    command.add_argument("run_id")
    command.add_argument("--timeout", type=int, default=7200)
    command.add_argument("--interval", type=int, default=15)
    args = parser.parse_args()
    result = {"submit": submit, "monitor": monitor}[args.op](args)
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
