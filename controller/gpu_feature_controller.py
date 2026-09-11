#!/usr/bin/env python3
"""Submit and prove actual two-node ABACUS cuSOLVERMp and NCCL execution."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import time

from remote_controller import TARGETS, safe_name
from runtime_controller import build_artifact
from source_cache import checksum

ROOT = Path(os.environ.get("SAI_SOFTWARE_ROOT", Path.home() / "sai-hpc-software")).resolve()
CONTROL = Path(__file__).resolve().parent
GPU_TARGETS = {"4v100-avx512", "16v100-avx2", "8v100v0-avx512"}
ENERGIES = {"cusolvermp": -196.6221723701324322, "nccl": -4869.7470518349809936}
# Buffer-size queries and --info/build flags are deliberately not execution
# evidence. Real library logging must show the generalized eigensolve itself.
EIGENSOLVE = re.compile(r"\bcusolverMp(?:Sygvd|Hegvd)\b", re.IGNORECASE)
COLLECTIVE = re.compile(r"\b(AllGather|AllReduce|Broadcast): opCount\s+[^\n]*\[nranks=2\]")


def call(argv, **kwargs):
    return subprocess.run([str(value) for value in argv], check=True, text=True, **kwargs)


def run_dir(run_id):
    path = ROOT / "runtime-tests" / safe_name(run_id)
    if path.resolve() != path:
        raise ValueError("GPU feature test path must not be a symlink")
    return path


def resources(args):
    if (args.target not in GPU_TARGETS or args.nodes != 2 or args.gpus_per_node != 1):
        raise ValueError("GPU feature acceptance requires two nodes and one GPU per node")
    return {"nodes": 2, "ranks": 2, "ranks_per_node": 1, "gpus_per_node": 1}


def render_job(args):
    resources(args)
    safe_name(args.version)
    target = TARGETS[args.target]
    task = run_dir(args.run_id)
    q = shlex.quote
    runtime = CONTROL / "gpu_feature_runtime.sh"
    prefix = f"/opt/software/abacus/{args.version}/{args.target}"
    lines = [
        "#!/usr/bin/env bash",
        f"#SBATCH --job-name=gpu-features-abacus-{safe_name(args.run_id)}",
        f"#SBATCH --partition={target['partition']}",
        f"#SBATCH --qos={target['qos']}",
        "#SBATCH --nodes=2", "#SBATCH --ntasks=2",
        "#SBATCH --ntasks-per-node=1", "#SBATCH --gpus-per-node=1",
        f"#SBATCH --time={args.minutes}",
        f"#SBATCH --output={task}/results/slurm-%j.log", "#SBATCH --export=NIL",
        "set -euo pipefail",
        f"export HOME={q(str(Path.home()))}",
        "export USER=${SLURM_JOB_USER:?}", "export LOGNAME=$USER",
        "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        'export LD_LIBRARY_PATH="" LD_PRELOAD=""',
        f"export TMPDIR={q(str(task / 'mpi-runtime'))}",
        f"export APPTAINER_TMPDIR={q(str(task / 'apptainer-runtime'))}",
        f"export APPTAINER_CACHEDIR={q(str(task / 'apptainer-cache'))}",
        "unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH",
        "source /etc/profile.d/lmod.sh", "module purge",
        "module use /opt/modules/modulefiles/devtools",
        "module load apptainer/1.4.4 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto",
        f"source /opt/sai_config/mps_mapping.d/{target['partition']}.bash",
        f"export SAI_SOFTWARE_ROOT={q(str(ROOT))}",
        f"export SAI_ABACUS_VERSION={q(args.version)}",
        "export SLURM_EXPORT_ENV=ALL",
        "export OMPI_MCA_plm_slurm_args=--external-launcher",
        "export PRTE_MCA_plm_slurm_args=--external-launcher",
        f"cd {q(str(task))}",
        f"bash {q(str(runtime))} {q(str(task))} {q(args.artifact)} {q(args.launcher)} "
        f"{q(prefix)} {q(str(CONTROL / 'gpu_feature_controller.py'))} {q(args.run_id)}",
    ]
    return "\n".join(lines) + "\n"


def submit(args):
    allocation = resources(args)
    task = run_dir(args.run_id)
    if task.exists():
        raise ValueError("GPU feature test already exists; choose a fresh run id")
    artifact = build_artifact(args.build_run_id, args.version, args.target)
    launcher = CONTROL / "abacus"
    runtime = CONTROL / "gpu_feature_runtime.sh"
    for path in (launcher, runtime):
        if not path.is_file() or path.is_symlink():
            raise ValueError("trusted GPU feature launcher is missing")
    if not os.access(launcher, os.X_OK):
        raise ValueError("trusted ABACUS launcher is not executable")
    for name in ("results", "apptainer-runtime", "apptainer-cache", "mpi-runtime"):
        (task / name).mkdir(parents=True)
    args.artifact, args.launcher = str(artifact), str(launcher)
    request = dict(vars(args), **{k: v for k, v in allocation.items() if k not in vars(args)})
    request.update(artifact_sha256=checksum(artifact), launcher_sha256=checksum(launcher),
                   controller_sha256=checksum(Path(__file__).resolve()),
                   runtime_sha256=checksum(runtime))
    script = task / "job.sbatch"
    script.write_text(render_job(args))
    script.chmod(0o700)
    call(["bash", "-n", script])
    request["job_script_sha256"] = checksum(script)
    (task / "request.json").write_text(json.dumps(request, sort_keys=True) + "\n")
    result = call(["sbatch", "--parsable", script], capture_output=True)
    job = result.stdout.strip().split(";")[0]
    if not job.isdigit():
        raise ValueError("invalid sbatch response")
    (task / "job.id").write_text(job + "\n")
    print(job, flush=True)


def trusted_request(task):
    request = json.loads((task / "request.json").read_text())
    resources(argparse.Namespace(**request))
    artifact = Path(request["artifact"])
    expected = (ROOT / "containers/software/abacus" / safe_name(request["version"]) /
                request["target"] / f"{safe_name(request['build_run_id'])}.sif")
    if artifact != expected or artifact.resolve() != expected:
        raise ValueError("GPU feature artifact reference changed")
    launcher = Path(request["launcher"])
    if launcher != CONTROL / "abacus":
        raise ValueError("GPU feature launcher is not the trusted launcher")
    for path, key in ((artifact, "artifact_sha256"), (launcher, "launcher_sha256"),
                      (Path(__file__).resolve(), "controller_sha256"),
                      (CONTROL / "gpu_feature_runtime.sh", "runtime_sha256"),
                      (task / "job.sbatch", "job_script_sha256")):
        if not path.is_file() or path.is_symlink() or checksum(path) != request[key]:
            raise ValueError(f"GPU feature {key} changed")
    return request


def verify_evidence(task, request):
    """Re-read scientific output, topology, and runtime library calls; fail closed."""
    files = {}

    def read_evidence(path):
        if not path.is_file() or path.is_symlink() or path.resolve() != path:
            raise ValueError(f"missing or untrusted GPU evidence: {path}")
        files[str(path.relative_to(task))] = checksum(path)
        return path.read_text(errors="replace")

    # Publication calls this function independently of monitor: bind its raw
    # evidence to the exact submitted script as well as the artifact and job.
    read_evidence(task / "job.sbatch")
    if files["job.sbatch"] != request["job_script_sha256"]:
        raise ValueError("GPU feature job_script_sha256 changed")
    artifact = Path(request["artifact"])
    if (not artifact.is_file() or artifact.is_symlink() or artifact.resolve() != artifact or
            checksum(artifact) != request["artifact_sha256"]):
        raise ValueError("GPU feature artifact_sha256 changed")
    job = read_evidence(task / "job.id").strip()
    if not job.isdigit():
        raise ValueError("invalid GPU feature evidence job id")
    energies = {}
    matched = {}
    nccl_collectives = set()
    for feature, expected_energy in ENERGIES.items():
        result = task / "results" / feature
        case = task / "cases" / feature
        rank_files = sorted((result / "ranks").glob("rank-*.tsv"))
        if len(rank_files) != 2 or any(path.is_symlink() for path in rank_files):
            raise ValueError(f"{feature}: expected exactly two rank traces")
        rows = [read_evidence(path).rstrip("\n").split("\t") for path in rank_files]
        mpi_isa = TARGETS[request["target"]]["dependency_isa"]
        if (any(len(row) != 6 or not row[0] or row[2] != request["target"] or
                row[3] != request["artifact"] or not row[4] or
                not row[5].endswith("-" + mpi_isa) for row in rows) or
                {row[1] for row in rows} != {"0", "1"} or
                len({row[0] for row in rows}) != 2):
            raise ValueError(f"{feature}: invalid two-node GPU rank evidence")
        scf = read_evidence(case / "OUT.autotest/running_scf.log")
        if "#SCF IS CONVERGED#" not in scf:
            raise ValueError(f"{feature}: SCF did not converge")
        values = re.findall(r"!FINAL_ETOT_IS\s+(\S+)", scf)
        try:
            energy = float(values[-1])
        except (IndexError, ValueError) as error:
            raise ValueError(f"{feature}: missing numeric final energy") from error
        if not math.isfinite(energy) or abs(energy - expected_energy) > 1e-5:
            raise ValueError(f"{feature}: wrong final energy: {energy}")
        energies[feature] = energy
        log = read_evidence(result / "abacus.log")
        pattern = EIGENSOLVE if feature == "cusolvermp" else COLLECTIVE
        evidence = [line for line in log.splitlines() if pattern.search(line)]
        if feature == "cusolvermp":
            evidence = [line for line in evidence if not re.search(r"bufferSize|ERROR", line, re.I)]
        if not evidence:
            raise ValueError(f"{feature}: no actual distributed library execution evidence")
        if feature == "nccl":
            nccl_collectives.update(COLLECTIVE.search(line).group(1) for line in evidence)
        matched[feature] = evidence[:4]
    return {"verified": True, "job": job, "artifact_sha256": request["artifact_sha256"],
            "nodes": 2, "ranks": 2, "nccl_collective": True,
            "nccl_collectives": sorted(nccl_collectives),
            "cusolvermp_eigensolve": True, "final_energy_ev": energies,
            "execution_evidence": matched, "files": files}


def verify(args):
    task = run_dir(args.run_id)
    evidence = verify_evidence(task, trusted_request(task))
    (task / "results/evidence.json").write_text(json.dumps(evidence, sort_keys=True) + "\n")
    print(json.dumps(evidence, sort_keys=True), flush=True)


def update_proof(task, run_id, proof=None):
    # A failed/repeated monitor invalidates only its own earlier proof. Identity
    # checks remain strict, even if execution or a later checksum check failed.
    request = json.loads((task / "request.json").read_text())
    artifact = Path(request["artifact"])
    expected = (ROOT / "containers/software/abacus" / safe_name(request["version"]) /
                safe_name(request["target"]) / f"{safe_name(request['build_run_id'])}.sif")
    sidecar = artifact.with_suffix(".json")
    if artifact != expected or artifact.resolve() != expected or sidecar.is_symlink():
        raise ValueError("GPU feature manifest reference changed")
    manifest = json.loads(sidecar.read_text())
    if proof is not None:
        if manifest.get("sha256") != request["artifact_sha256"]:
            raise ValueError("GPU feature manifest checksum changed")
        manifest["gpu_features"] = proof
    elif manifest.get("gpu_features", {}).get("run_id") == run_id:
        manifest.pop("gpu_features")
    else:
        return
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
        queued = subprocess.run(["squeue", "-h", "-j", job, "-o", "%T|%R"],
                                check=False, text=True, capture_output=True).stdout.strip()
        if queued:
            if queued != previous:
                print(f"{job}: {queued}", flush=True)
                previous = queued
        else:
            rows = call(["sacct", "-X", "-n", "-P", "-j", job,
                         "-o", "JobIDRaw,State,ExitCode"], capture_output=True).stdout.splitlines()
            values = next((row.split("|") for row in rows if row.split("|")[0] == job), None)
            if values and values[1] not in ("RUNNING", "PENDING", "COMPLETING"):
                status = {"job": job, "state": values[1], "exit_code": values[2], "verified": False}
                status_path = task / "results/status.json"
                status_path.write_text(json.dumps(status) + "\n")
                update_proof(task, args.run_id)
                success = values[1] == "COMPLETED" and values[2] == "0:0"
                if success:
                    request = trusted_request(task)
                    evidence = verify_evidence(task, request)
                    evidence_path = task / "results/evidence.json"
                    evidence_path.write_text(json.dumps(evidence, sort_keys=True) + "\n")
                    proof = dict(evidence, run_id=args.run_id,
                                 partition=TARGETS[request["target"]]["partition"],
                                 controller_sha256=request["controller_sha256"],
                                 launcher=request["launcher"], launcher_sha256=request["launcher_sha256"],
                                 runtime_sha256=request["runtime_sha256"],
                                 job_script_sha256=request["job_script_sha256"],
                                 evidence_sha256=checksum(evidence_path))
                    update_proof(task, args.run_id, proof)
                    status["verified"] = True
                    status_path.write_text(json.dumps(status) + "\n")
                print("|".join(values), flush=True)
                return 0 if success else 1
        time.sleep(args.interval)
    raise TimeoutError(f"monitor deadline reached; job {job} was not cancelled")


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="op", required=True)
    command = commands.add_parser("submit")
    command.add_argument("run_id")
    command.add_argument("version")
    command.add_argument("target", choices=sorted(GPU_TARGETS))
    command.add_argument("--build-run-id", required=True)
    command.add_argument("--nodes", type=int, default=2)
    command.add_argument("--gpus-per-node", type=int, default=1)
    command.add_argument("--minutes", type=int, default=30)
    command = commands.add_parser("monitor")
    command.add_argument("run_id")
    command.add_argument("--timeout", type=int, default=7200)
    command.add_argument("--interval", type=int, default=15)
    command = commands.add_parser("verify")
    command.add_argument("run_id")
    args = parser.parse_args()
    result = {"submit": submit, "monitor": monitor, "verify": verify}[args.op](args)
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
