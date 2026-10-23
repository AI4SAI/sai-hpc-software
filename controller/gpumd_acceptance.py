#!/usr/bin/env python3
"""Fail-closed GPUMD scientific/parity/benchmark publication proof."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import time

from remote_controller import site_app_binds, TARGETS, safe_name, container_command, project_batch_directives
from source_cache import checksum
from delivery_layout import artifact_path, load_artifact, validate_record
from release_contract import GPU_TARGETS, validate_identity

ROOT = Path(os.environ.get("SAI_SOFTWARE_ROOT", Path.home() / "sai-hpc-software")).resolve()
CONTROL = Path(__file__).resolve().parent


def regular(path):
    if not path.is_file() or path.is_symlink() or path.resolve() != path:
        raise ValueError(f"missing or untrusted file: {path}")
    return path


def run_dir(run_id):
    task = ROOT / "runtime-tests" / safe_name(run_id)
    if task.resolve() != task:
        raise ValueError("untrusted GPUMD acceptance path")
    return task


def render_job(request, task):
    identity = validate_identity(request["identity"])
    if (identity["software"] != "gpumd" or identity["target"] != request["target"] or
            identity["source_version"] != request["version"] or
            artifact_path(ROOT, identity, request["build_run_id"]) != Path(request["artifact"])):
        raise ValueError("GPUMD scientific request differs from its canonical identity")
    target = TARGETS[request["target"]]
    prefix = identity["install_prefix"]
    q = shlex.quote
    # A contained RW work directory for outputs/JIT only. Installation is RO.
    science_args = " --repeat-source /previous" if request.get("repeat_of") else ""
    argv = container_command(request["artifact"], ["/usr/bin/bash", "--noprofile", "--norc", "-c",
        'driver=${LD_LIBRARY_PATH:-}; source "$1"; export LD_LIBRARY_PATH="$driver${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"; '
        'export CUDACXX="$CUDA_HOME/bin/nvcc"; exec /usr/bin/python3 /control/gpumd_science.py run "$2" /work/science' + science_args,
        "bash", f"{prefix}/share/sai/runtime-env.sh", prefix],
        control=CONTROL, gpu=True, extra_binds=site_app_binds("gpumd"))
    # Insert trusted write binds/env before the image (the policy helper's
    # extra_binds is intentionally read-only for dependencies).
    if request.get("repeat_of"):
        previous = run_dir(request["repeat_of"]["run_id"]) / "results/science"
        argv[argv.index(request["artifact"]):argv.index(request["artifact"])] = ["--bind", f"{previous}:/previous:ro"]
    index = argv.index(request["artifact"])
    argv[index:index] = ["--bind", f"{task / 'results'}:/work:rw", "--bind", f"{task / 'runtime'}:/runtime:rw",
                        "--env", "TMPDIR=/runtime", "--env", f"SLURM_JOB_ID=${{SLURM_JOB_ID}}",
                        "--env", "CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES}"]
    command = shlex.join(argv).replace("'SLURM_JOB_ID=${SLURM_JOB_ID}'", '"SLURM_JOB_ID=${SLURM_JOB_ID}"').replace(
        "'CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES}'", '"CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES}"')
    lines = ["#!/usr/bin/env bash", f"#SBATCH --job-name=gpumd-science-{request['run_id']}",
        f"#SBATCH --partition={target['partition']}", f"#SBATCH --qos={target['qos']}",
        "#SBATCH --nodes=1", "#SBATCH --ntasks=1", "#SBATCH --gpus-per-node=1",
        "#SBATCH --time=40", f"#SBATCH --output={task}/results/slurm-%j.log", "#SBATCH --export=NIL",
        *project_batch_directives(ROOT, task),
        "set -eo pipefail", "export PATH=/usr/bin:/bin LD_LIBRARY_PATH= LD_PRELOAD=",
        "source /etc/profile.d/lmod.sh", "module load apptainer/1.4.4", "set -u",
        f"source /opt/sai_config/mps_mapping.d/{target['partition']}.bash",
        f"export TMPDIR={q(str(task / 'runtime'))} APPTAINER_TMPDIR={q(str(task / 'runtime'))}",
        f"export APPTAINER_CACHEDIR={q(str(task / 'cache'))}",
        "unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH",
        f"nvidia-smi --query-gpu=name,uuid,driver_version --format=csv,noheader > {q(str(task / 'results/gpu-host.txt'))}", command,
        f"mkdir {q(str(task / 'results/launcher'))}",
        f"cp {q(str(task / 'results/science/static-candidate/model.xyz'))} {q(str(task / 'results/science/static-candidate/nep.txt'))} "
        f"{q(str(task / 'results/science/static-candidate/run.in'))} {q(str(task / 'results/launcher'))}/",
        f"export SAI_SOFTWARE_ROOT={q(str(ROOT))}",
        f"export SAI_GPUMD_IMAGE={q(request['artifact'])}",
        f"cd {q(str(task / 'results/launcher'))}", f"{q(request['launcher'])} > run.log 2>&1",
        "echo GPUMD_SCIENCE_AND_LAUNCHER_COMPLETED"]
    return "\n".join(lines) + "\n"


def submit(args):
    if args.target not in GPU_TARGETS:
        raise ValueError("GPUMD acceptance requires its native GPU target")
    task = run_dir(args.run_id)
    if task.exists():
        raise ValueError("GPUMD acceptance run already exists")
    build = ROOT / "runs" / safe_name(args.build_run_id)
    artifact = Path(regular(build / "artifact.path").read_text().strip())
    manifest = load_artifact(ROOT, artifact, software="gpumd", target=args.target)
    identity = manifest["identity"]
    if (artifact != artifact_path(ROOT, identity, args.build_run_id) or
            identity["source_version"] != args.version):
        raise ValueError("build candidate path mismatch")
    request = dict(vars(args), identity=identity, artifact=str(artifact), artifact_sha256=manifest["sha256"],
                   launcher=str(CONTROL / "gpumd"), launcher_sha256=checksum(regular(CONTROL / "gpumd")),
                   controller_sha256=checksum(CONTROL / "gpumd_acceptance.py"),
                   science_sha256=checksum(CONTROL / "gpumd_science.py"),
                   deepmd_probe_sha256=checksum(CONTROL / "gpumd_deepmd_probe.py"))
    if args.run_id.endswith("-retest"):
        original_id = args.run_id.removesuffix("-retest")
        original, original_evidence = completed_evidence(original_id)
        require_original_regression(original_evidence)
        check_repeat_request(request, original)
        request["repeat_of"] = repeat_binding(original_id, original_evidence)
    for name in ("results", "runtime", "cache"):
        (task / name).mkdir(parents=True)
    script = task / "job.sbatch"
    script.write_text(render_job(request, task))
    request["job_script_sha256"] = checksum(script)
    (task / "request.json").write_text(json.dumps(request, sort_keys=True) + "\n")
    subprocess.run(["bash", "-n", script], check=True)
    job = subprocess.check_output(["sbatch", "--parsable", script], text=True, cwd=task).strip().split(";")[0]
    if not job.isdigit():
        raise ValueError("invalid Slurm job id")
    (task / "job.id").write_text(job + "\n")
    print(job, flush=True)


def verify_evidence(task, request):
    from gpumd_science import compare, xyz, recheck_results, required_results
    if task != run_dir(request["run_id"]):
        raise ValueError("GPUMD evidence is stored under a different run id")
    artifact = regular(Path(request["artifact"]))
    manifest = load_artifact(ROOT, artifact, software="gpumd", target=request["target"])
    identity = validate_identity(request["identity"])
    if (manifest["identity"] != identity or manifest["sha256"] != request["artifact_sha256"] or
            artifact != artifact_path(ROOT, identity, request["build_run_id"]) or
            identity["source_version"] != request["version"]):
        raise ValueError("GPUMD evidence differs from its pinned artifact identity")
    for path, key in ((task / "job.sbatch", "job_script_sha256"),
                      (CONTROL / "gpumd", "launcher_sha256"), (CONTROL / "gpumd_acceptance.py", "controller_sha256"),
                      (CONTROL / "gpumd_science.py", "science_sha256"),
                      (CONTROL / "gpumd_deepmd_probe.py", "deepmd_probe_sha256")):
        if checksum(regular(path)) != request[key]:
            raise ValueError(f"GPUMD evidence changed: {key}")
    job = regular(task / "job.id").read_text().strip()
    science_file = regular(task / "results/science/science.json")
    science = json.loads(science_file.read_text())
    if science.get("identity") != identity:
        raise ValueError("GPUMD scientific results have a different delivery identity")
    if checksum(regular(task / "results/gpu-host.txt")) != science["gpu_metadata_sha256"]:
        raise ValueError("GPU allocation metadata changed")
    required = {"static-candidate", "static-baseline", "nep-jit-vs-generic-loss", "gnep-training-and-prediction",
                "plumed-force-feedback", "gnep-finite-difference-forces", "deepmd-candidate", "deepmd-baseline",
                "installed-jit-resources-from-unrelated-cwd"}
    if not required <= science["checks"].keys() or science["job"] != job or not science["gpu_visible"]:
        raise ValueError("missing GPUMD numerical/feature evidence")
    if not science["files"]:
        raise ValueError("missing raw GPUMD scientific evidence")
    if not required_results() <= science["files"].keys():
        raise ValueError("missing mandatory raw GPUMD paths in evidence manifest")
    for name, expected in science["files"].items():
        path = task / "results/science" / name
        if Path(name).is_absolute() or ".." in Path(name).parts or checksum(regular(path)) != expected:
            raise ValueError("GPUMD raw numerical output changed")
    recomputed_checks, recomputed_benchmark = recheck_results(task / "results/science", science)
    if recomputed_checks != science["checks"] or recomputed_benchmark != science["benchmark"]:
        raise ValueError("GPUMD summary differs from raw scientific outputs or benchmark logs")
    for mode in ("candidate", "baseline"):
        for kind in ("static", "prediction"):
            benchmark = science["benchmark"][f"{kind}-{mode}"]
            if len(benchmark["seconds"]) != 3 or benchmark["warmup"] < 1 or any(value <= 0 for value in benchmark["seconds"]):
                raise ValueError("missing repeated same-allocation benchmark")
        throughput = science["benchmark"][f"md-throughput-{mode}"]
        if (throughput["n_atoms"] != 2000 or throughput["n_steps"] < 10000 or
                throughput["warmup"] < 1 or len(throughput["samples"]) != 3 or
                any(row["engine_seconds"] <= 0 or row["atom_steps_per_second"] <= 0 for row in throughput["samples"])):
            raise ValueError("missing meaningful MD engine benchmark")
    for key in ("input_sha256", "model_sha256", "n_atoms", "n_steps"):
        if science["benchmark"]["md-throughput-candidate"][key] != science["benchmark"]["md-throughput-baseline"][key]:
            raise ValueError("throughput comparison used different inputs")
    energy, forces = xyz(regular(task / "results/launcher/dump.xyz"))
    gold_e, gold_f = xyz(regular(task / "results/science/static-candidate/gold.xyz"))
    compare([[energy]], [[gold_e]], 1e-3, "host launcher energy")
    compare(forces, gold_f, 1e-4, "host launcher forces")
    result = {"verified": True, "job": job, "identity": identity,
            "artifact_sha256": request["artifact_sha256"],
            "science_sha256": checksum(science_file),
            "launcher_result_sha256": checksum(task / "results/launcher/dump.xyz"),
            "checks": science["checks"], "benchmark": science["benchmark"]}
    if request.get("run_id", "").endswith("-retest") or request.get("repeat_of"):
        result["repeat_of"] = verify_repeat(task, request, science)
    elif science.get("repeat_of"):
        raise ValueError("GPUMD original run cannot claim repeat calibration")
    return result


def performance_gate(evidence):
    from delivery_layout import check_performance
    timings = evidence["benchmark"]
    return check_performance(1 / timings["md-throughput-candidate"]["median_atom_steps_per_second"],
                             1 / timings["md-throughput-baseline"]["median_atom_steps_per_second"])


def require_original_regression(evidence):
    from delivery_layout import PerformanceRegression
    try:
        performance_gate(evidence)
    except PerformanceRegression:
        return
    raise ValueError("GPUMD retest requires an original regression greater than 10%")


def completed_evidence(run_id):
    task = run_dir(run_id)
    request = json.loads(regular(task / "request.json").read_text())
    if request.get("run_id") != run_id or request.get("repeat_of") or run_id.endswith("-retest"):
        raise ValueError("GPUMD retest requires the first original acceptance run")
    evidence = verify_evidence(task, request)
    status = json.loads(regular(task / "results/status.json").read_text())
    if (status != {"job": evidence["job"], "state": "COMPLETED", "exit_code": "0:0", "verified": True} or
            json.loads(regular(task / "results/evidence.json").read_text()) != evidence):
        raise ValueError("GPUMD original raw evidence is not verified or has changed")
    return request, evidence


def repeat_binding(run_id, evidence):
    task = run_dir(run_id)
    return {"run_id": run_id, "job": evidence["job"], "science_sha256": evidence["science_sha256"],
            "request_sha256": checksum(regular(task / "request.json")),
            "evidence_sha256": checksum(regular(task / "results/evidence.json"))}


def check_repeat_request(request, original):
    keys = ("artifact", "artifact_sha256", "identity", "target", "version", "build_run_id", "launcher",
            "launcher_sha256", "controller_sha256", "science_sha256", "deepmd_probe_sha256")
    if any(request.get(key) != original.get(key) for key in keys):
        raise ValueError("GPUMD retest changed its image, source, controller or resources")


def gpu_configuration(text):
    devices = []
    for line in text.splitlines():
        fields = [value.strip() for value in line.split(",")]
        if len(fields) != 3 or not all(fields):
            raise ValueError("invalid GPUMD GPU metadata")
        devices.append((fields[0], fields[2]))
    if not devices:
        raise ValueError("missing GPUMD GPU metadata")
    return sorted(devices)


def verify_repeat(task, request, science):
    repeat = request.get("repeat_of", {})
    original_id = request["run_id"].removesuffix("-retest")
    if request["run_id"] != original_id + "-retest" or repeat.get("run_id") != original_id:
        raise ValueError("GPUMD retest is not bound to its original run")
    original_request, original_evidence = completed_evidence(original_id)
    require_original_regression(original_evidence)
    check_repeat_request(request, original_request)
    if repeat != repeat_binding(original_id, original_evidence):
        raise ValueError("GPUMD retest binding changed after submission")
    original_task = run_dir(original_id) / "results/science"
    original_science = json.loads(regular(original_task / "science.json").read_text())
    for key in ("executables", "packaged_inputs", "conditions"):
        if not original_science.get(key) or science.get(key) != original_science[key]:
            raise ValueError("GPUMD retest changed its binaries, scientific inputs or resources")
    if gpu_configuration(science["gpu"]) != gpu_configuration(original_science["gpu"]):
        raise ValueError("GPUMD retest changed its GPU configuration")
    from gpumd_science import repeat_inputs
    for relative in repeat_inputs():
        if checksum(regular(task / "results/science" / relative)) != checksum(regular(original_task / relative)):
            raise ValueError("GPUMD retest changed frozen scientific inputs")
    steps = original_evidence["benchmark"]["md-throughput-baseline"]["n_steps"]
    if science.get("repeat_of") != {"science_sha256": original_evidence["science_sha256"], "n_steps": steps}:
        raise ValueError("GPUMD retest changed the original calibrated workload")
    return repeat


def validate_manifest(manifest, root=None, control=None):
    global ROOT, CONTROL
    ROOT, CONTROL = Path(root or ROOT), Path(control or CONTROL)
    if manifest["target"] not in GPU_TARGETS:
        raise ValueError("unregistered GPUMD target")
    proof = manifest.get("gpumd_science", {})
    if proof.get("verified") is not True:
        raise ValueError("missing GPUMD scientific/parity/benchmark proof")
    identity = validate_record(manifest)
    task = run_dir(proof["run_id"])
    request = json.loads(regular(task / "request.json").read_text())
    status = json.loads(regular(task / "results/status.json").read_text())
    if (request.get("identity") != identity or proof.get("identity") != identity or
            request["artifact"] != manifest["artifact"] or request["artifact_sha256"] != manifest["sha256"] or
            request["target"] != manifest["target"] or request["launcher"] != str(CONTROL / "gpumd") or
            status != {"job": proof["job"], "state": "COMPLETED", "exit_code": "0:0", "verified": True}):
        raise ValueError("GPUMD proof does not match the accepted job/image")
    evidence = verify_evidence(task, request)
    if json.loads(regular(task / "results/evidence.json").read_text()) != evidence or any(
            proof.get(key) != value for key, value in evidence.items()):
        raise ValueError("stale GPUMD science evidence")
    performance_gate(evidence)
    if proof.get("launcher_sha256") != request["launcher_sha256"]:
        raise ValueError("untested GPUMD launcher")


def monitor(args):
    task = run_dir(args.run_id)
    job = regular(task / "job.id").read_text().strip()
    if not job.isdigit():
        raise ValueError("invalid job id")
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        queue = subprocess.run(["squeue", "-h", "-j", job, "-o", "%T"], text=True, capture_output=True, check=True).stdout.strip()
        if queue:
            print(f"GPUMD {job}: {queue}", flush=True)
        else:
            rows = subprocess.check_output(["sacct", "-X", "-n", "-P", "-j", job, "-o", "JobIDRaw,State,ExitCode"], text=True)
            row = next((line.split("|") for line in rows.splitlines() if line.split("|")[0] == job), None)
            if row and row[1] not in ("RUNNING", "PENDING", "COMPLETING"):
                status = {"job": job, "state": row[1], "exit_code": row[2], "verified": False}
                (task / "results/status.json").write_text(json.dumps(status) + "\n")
                if row[1:3] != ["COMPLETED", "0:0"]:
                    return 1
                request = json.loads(regular(task / "request.json").read_text())
                evidence = verify_evidence(task, request)
                (task / "results/evidence.json").write_text(json.dumps(evidence, sort_keys=True) + "\n")
                artifact = Path(request["artifact"])
                sidecar = artifact.with_suffix(".json")
                manifest = json.loads(regular(sidecar).read_text())
                if (manifest["sha256"] != request["artifact_sha256"] or
                        validate_record(manifest) != request["identity"]):
                    raise ValueError("candidate changed during acceptance")
                manifest["gpumd_science"] = dict(evidence, run_id=args.run_id,
                                                launcher_sha256=request["launcher_sha256"])
                from software_controller import atomic_manifest
                atomic_manifest(artifact, manifest)
                status["verified"] = True
                (task / "results/status.json").write_text(json.dumps(status) + "\n")
                print(json.dumps(evidence, sort_keys=True), flush=True)
                return 0
        time.sleep(20)
    raise TimeoutError(f"GPUMD job {job} was not cancelled; re-monitor this existing job")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    operations = parser.add_subparsers(dest="op", required=True)
    submit_parser = operations.add_parser("submit")
    for name in ("run_id", "version", "target"):
        submit_parser.add_argument(name)
    submit_parser.add_argument("--build-run-id", required=True)
    monitor_parser = operations.add_parser("monitor")
    monitor_parser.add_argument("run_id")
    monitor_parser.add_argument("--timeout", type=int, default=7200)
    args = parser.parse_args()
    result = {"submit": submit, "monitor": monitor}[args.op](args)
    raise SystemExit(result or 0)
