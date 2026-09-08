#!/usr/bin/env python3
"""Submit and monitor host-MPI, multi-node tests of published SIF artifacts."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import time

from remote_controller import TARGETS, safe_name
from source_cache import checksum

ROOT = Path(os.environ.get("SAI_SOFTWARE_ROOT", Path.home() / "sai-hpc-software")).resolve()
CONTROL = Path(__file__).resolve().parent
MAPPING_ROOT = Path("/opt/sai_config/mps_mapping.d")
RUNTIME_TARGETS = {"4v100-avx512", "16v100-avx2"}


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
    safe_name(version)
    build_task = ROOT / "runs" / build_run_id
    artifact_file = build_task / "artifact.path"
    if not artifact_file.is_file() or artifact_file.is_symlink():
        raise ValueError("build run has no trusted artifact reference")
    artifact = Path(artifact_file.read_text().strip())
    expected = ROOT / "containers/software/abacus" / version / target / f"{build_run_id}.sif"
    if (artifact != expected or artifact.resolve() != expected or
            not artifact.is_file() or artifact.is_symlink()):
        raise ValueError("build artifact does not match the requested run")
    sidecar = artifact.with_suffix(".json")
    if not sidecar.is_file() or sidecar.is_symlink():
        raise ValueError("build artifact has no trusted manifest")
    manifest = json.loads(sidecar.read_text())
    if (manifest.get("verified") is not True or manifest.get("artifact") != str(artifact) or
            manifest.get("version") != version or manifest.get("target") != target or
            manifest.get("sha256") != checksum(artifact)):
        raise ValueError("build artifact manifest is invalid")
    return artifact


def render_job(args):
    safe_name(args.version)
    target = TARGETS[args.target]
    task = run_dir(args.run_id)
    current = ROOT / "containers/software/abacus" / args.version / args.target / "current.sif"
    artifact = Path(getattr(args, "artifact", current)).resolve()
    module_root = ROOT / "modulefiles/apps"
    prefix = f"/opt/software/abacus/{args.version}/{args.target}"
    if args.target not in RUNTIME_TARGETS:
        raise ValueError("multi-node runtime acceptance is registered only for precise V100 targets")
    if args.nodes != 2 or args.gpus_per_node != 1:
        raise ValueError("runtime resources outside acceptance bounds")
    q = shlex.quote
    ranks = args.nodes * args.gpus_per_node
    results = task / "results"
    case = task / "case"
    runtime = task / "apptainer-runtime"
    mapping = MAPPING_ROOT / (target["partition"] + ".bash")
    mpi_isa = "avx512" if args.target == "4v100-avx512" else "avx2"
    lines = [
        "#!/usr/bin/env bash",
        f"#SBATCH --job-name=runtime-abacus-{args.run_id}",
        f"#SBATCH --partition={target['partition']}",
        f"#SBATCH --qos={target['qos']}",
        f"#SBATCH --nodes={args.nodes}",
        f"#SBATCH --ntasks={ranks}",
        f"#SBATCH --ntasks-per-node={args.gpus_per_node}",
        f"#SBATCH --gpus-per-node={args.gpus_per_node}",
        f"#SBATCH --time={args.minutes}",
        f"#SBATCH --output={results}/slurm-%j.log",
        "#SBATCH --export=NIL",
        "set -euo pipefail",
        f"export HOME={q(str(Path.home()))}",
        "export USER=${SLURM_JOB_USER:?} LOGNAME=$USER",
        "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        f"export TMPDIR={q(str(runtime))} APPTAINER_TMPDIR={q(str(runtime))}",
        f"export APPTAINER_CACHEDIR={q(str(task / 'apptainer-cache'))}",
        "unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH",
        "source /etc/profile.d/lmod.sh",
        "module purge",
        f"module use {q(str(module_root))}",
        f"module load {q('abacus/' + args.version)}",
        f"cd {q(str(task))}",
        f"source {q(str(mapping))}",
        "export MAP_OPT SLURM_EXPORT_ENV=ALL",
        "export OMPI_MCA_plm_slurm_args=--external-launcher",
        "export PRTE_MCA_plm_slurm_args=--external-launcher",
        f"image={q(str(artifact))}",
        'test -r "$image"',
        f"mkdir -p {q(str(results / 'ranks'))} {q(str(case))} {q(str(runtime))}",
        "apptainer exec --cleanenv --no-home "
        "--no-mount bind-paths,home,cwd,tmp,hostfs --pwd /work "
        "--bind /usr:/usr:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro "
        f"--bind {q(str(case))}:/work:rw \"$image\" /usr/bin/cp -a "
        f"{q(prefix + '/share/sai/smoke-case/.')} /work/",
        f"cd {q(str(case))}",
        f"export SAI_ABACUS_TRACE_DIR={q(str(results / 'ranks'))}",
        'export SAI_ABACUS_IMAGE="$image"',
        "nvidia-smi -L",
        "command -v mpirun apptainer abacus",
        f"mpirun -np {ranks} --map-by \"$MAP_OPT\" --report-bindings abacus > {q(str(results / 'abacus.log'))} 2>&1",
        f"test \"$(find {q(str(results / 'ranks'))} -maxdepth 1 -name 'rank-*.tsv' -type f | wc -l)\" -eq {ranks}",
        f"test \"$(cut -f1 {q(str(results / 'ranks'))}/rank-*.tsv | sort -u | wc -l)\" -eq {args.nodes}",
        f"test \"$(cut -f3 {q(str(results / 'ranks'))}/rank-*.tsv | sort -u)\" = {q(args.target)}",
        f"test \"$(cut -f4 {q(str(results / 'ranks'))}/rank-*.tsv | sort -u)\" = \"$image\"",
        f"awk -F '\\t' 'NF != 6 || $5 == \"\" || $6 == \"\" {{ exit 1 }}' {q(str(results / 'ranks'))}/rank-*.tsv",
        f"awk -F '\\t' '$6 !~ /-{mpi_isa}$/ {{ exit 1 }}' {q(str(results / 'ranks'))}/rank-*.tsv",
        f"grep -q '#SCF IS CONVERGED#' {q(str(case / 'OUT.autotest/running_scf.log'))}",
        f"grep -Eq 'GPU.*\\(x{ranks}\\)' {q(str(case / 'OUT.autotest/running_scf.log'))}",
        f"actual=$(awk '/!FINAL_ETOT_IS/{{value=$2}} END{{print value}}' {q(str(case / 'OUT.autotest/running_scf.log'))})",
        "awk -v actual=\"$actual\" -v expected=-4869.7470519303351466 "
        "'BEGIN { delta=actual-expected; if (delta<0) delta=-delta; exit !(delta<=1.0) }'",
        f"printf '%s\\n' \"$actual\" > {q(str(results / 'final-energy-ev.txt'))}",
        f"cat {q(str(results / 'ranks'))}/rank-*.tsv",
        "echo MULTINODE_CONTAINER_MPI_VERIFIED",
    ]
    return "\n".join(lines) + "\n"


def submit(args):
    task = run_dir(args.run_id)
    if task.exists():
        raise ValueError("runtime test already exists; choose a fresh run id")
    for name in ("results", "apptainer-runtime", "apptainer-cache"):
        (task / name).mkdir(parents=True, exist_ok=True)
    artifact = build_artifact(args.build_run_id, args.version, args.target)
    args.artifact = str(artifact)
    script = task / "job.sbatch"
    script.write_text(render_job(args))
    script.chmod(0o700)
    call(["bash", "-n", script])
    result = call(["sbatch", "--parsable", script], capture_output=True)
    job = result.stdout.strip().split(";")[0]
    if not job.isdigit():
        raise ValueError("invalid sbatch response")
    (task / "job.id").write_text(job + "\n")
    request = dict(vars(args), artifact=str(artifact), artifact_sha256=checksum(artifact))
    (task / "request.json").write_text(json.dumps(request, sort_keys=True) + "\n")
    print(job, flush=True)


def monitor(args):
    task = run_dir(args.run_id)
    job = (task / "job.id").read_text().strip()
    if not job.isdigit():
        raise ValueError("invalid job id")
    deadline = time.monotonic() + args.timeout
    previous = None
    while time.monotonic() < deadline:
        queued = call(["squeue", "-h", "-j", job, "-o", "%T|%R"], capture_output=True).stdout.strip()
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
                if success:
                    try:
                        request = json.loads((task / "request.json").read_text())
                        artifact = Path(request["artifact"])
                        if (not artifact.is_file() or artifact.is_symlink() or
                                checksum(artifact) != request["artifact_sha256"]):
                            raise ValueError("runtime-tested artifact changed")
                        sidecar = artifact.with_suffix(".json")
                        manifest = json.loads(sidecar.read_text())
                        verification = {
                            "run_id": args.run_id, "job": job,
                            "partition": TARGETS[request["target"]]["partition"],
                            "nodes": request["nodes"],
                            "gpus_per_node": request["gpus_per_node"],
                            "ranks": request["nodes"] * request["gpus_per_node"],
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
    command.add_argument("--gpus-per-node", type=int, default=1)
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
