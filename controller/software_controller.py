#!/usr/bin/env python3
"""Trusted host controller. External code only runs behind the container policy."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
from remote_controller import TARGETS, safe_name, safe_sha, container_command
from source_cache import checksum

ROOT = Path(os.environ.get("SAI_SOFTWARE_ROOT", Path.home() / "sai-hpc-software")).resolve()
CONTROL = Path(__file__).resolve().parent

def call(argv, **kw):
    return subprocess.run([str(x) for x in argv], check=True, text=True, **kw)

def task_dir(run_id):
    safe_name(run_id)
    path = ROOT / "runs" / run_id
    if path.resolve() != path:
        raise ValueError("run path must not be a symlink")
    return path

def init(args):
    safe_name(args.software)
    r = task_dir(args.run_id)
    for part in ("input", "results", "runtime", "apptainer-cache"):
        (r / part).mkdir(parents=True, exist_ok=True)
    return r

def render_job(args):
    r = task_dir(args.run_id)
    target = TARGETS[args.target]
    sha = safe_sha(args.sha)
    safe_name(args.version)
    if args.software != "abacus":
        raise ValueError("no trusted recipe registered for this software")
    if not 1 <= args.jobs <= 16 or not 1 <= args.minutes <= 180:
        raise ValueError("resource request outside controller bounds")
    if not 256 <= args.overlay_mb <= 32768:
        raise ValueError("overlay size outside policy bounds")
    image = ROOT / "containers/base/minimal-v1.sif"
    repo = ROOT / "cache/repositories" / args.software
    overlay = r / "work.ext3"
    squash = r / "final.squashfs"
    sif = r / "result.sif"
    artifact = ROOT / "containers/software" / args.software / args.version / args.target / (args.run_id + ".sif")
    # Host-provided, read-only interpreter; not a binary writable by a prior build.
    argv = ["/usr/bin/bash", "/control/container_entry.sh", "build", args.software, sha, args.version, args.target]
    def container(phase, final=False):
        cmd = argv.copy()
        cmd[2] = phase
        return container_command(sif if final else image, cmd, overlay=None if final else overlay,
                                 control=CONTROL, repository=None if final else repo,
                                 jobs=args.jobs, gpu=bool(target["gpus"]))
    emit = container_command(image, ["/usr/bin/cat", "/workspace/final.squashfs"],
                             overlay=str(overlay) + ":ro", jobs=args.jobs)
    q = shlex.quote
    lines = [
        "#!/bin/bash",
        f"#SBATCH --job-name=software-{args.software}-{args.run_id}",
        f"#SBATCH --partition={target['partition']}",
        f"#SBATCH --qos={target['qos']}",
        "#SBATCH --nodes=1", "#SBATCH --ntasks=1",
        f"#SBATCH --time={args.minutes}",
        f"#SBATCH --output={r}/results/slurm-%j.log",
        "#SBATCH --export=NIL",
    ]
    if target["gpus"]:
        lines += [f"#SBATCH --gpus-per-node={target['gpus']}"]
    else:
        # SAI GPU partitions assign CPU/memory from GPU count and prohibit
        # overriding these resources. CPU-MISC accepts explicit CPU/memory.
        lines += [f"#SBATCH --cpus-per-task={args.jobs}", "#SBATCH --mem=28G"]
    resume = getattr(args, "resume_run", None)
    prepare = [shlex.join(["apptainer", "overlay", "create", "--fakeroot", "--sparse",
                          "--size", str(args.overlay_mb), str(overlay)]),
               shlex.join(container("build"))]
    if resume:
        old_overlay = task_dir(resume) / "work.ext3"
        prepare = [f"mv -- {q(str(old_overlay))} {q(str(overlay))}", shlex.join(container("metadata"))]
    lines += [
        "set -eo pipefail",
        "export PATH=/usr/bin:/bin",
        'export LD_LIBRARY_PATH="" LD_PRELOAD=""',
        "source /etc/profile.d/lmod.sh", "module load apptainer/1.4.4",
        "set -u", "umask 077",
        f"export TMPDIR={q(str(r / 'runtime'))} APPTAINER_TMPDIR={q(str(r / 'runtime'))}",
        f"export APPTAINER_CACHEDIR={q(str(r / 'apptainer-cache'))}",
        "unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH",
        f"test -s {q(str(image))}",
        f"test ! -e {q(str(overlay))}",
        *prepare,
        shlex.join(container("export")),
        shlex.join(emit) + " > " + q(str(squash)),
        shlex.join(["apptainer", "sif", "new", str(sif)]),
        shlex.join(["apptainer", "sif", "add", "--datatype", "4", "--partfs", "1",
                    "--parttype", "2", "--partarch", "2", "--groupid", "1", str(sif), str(squash)]),
        shlex.join(container("verify", final=True)),
        f"mkdir -p {q(str(artifact.parent))}",
        f"test ! -e {q(str(artifact))}",
        f"chmod 444 {q(str(sif))}",
        f"mv {q(str(sif))} {q(str(artifact))}",
        # Remove only two exact generated files, never recurse over source/image contents.
        f"rm -- {q(str(squash))} {q(str(overlay))}",
        f"printf '%s\\n' {q(str(artifact))} > {q(str(r / 'artifact.path'))}",
        f"sha256sum {q(str(artifact))} > {q(str(r / 'results/artifact.sha256'))}",
        "echo BUILD_AND_ARTIFACT_VERIFIED",
    ]
    return "\n".join(lines) + "\n"

def submit(args):
    r = init(args)
    if (r / "job.id").exists():
        raise ValueError("run already submitted; choose a fresh run id")
    repo = ROOT / "cache/repositories" / args.software
    call(["git", "--git-dir", repo, "cat-file", "-e", safe_sha(args.sha) + "^{commit}"])
    if args.resume_run:
        old = task_dir(args.resume_run)
        previous = json.loads((old / "request.json").read_text())
        for key in ("software", "sha", "version", "target"):
            if previous[key] != getattr(args, key):
                raise ValueError(f"resume mismatch: {key}")
        old_job = (old / "job.id").read_text().strip()
        if not old_job.isdigit() or call(["squeue", "-h", "-j", old_job], capture_output=True).stdout.strip():
            raise ValueError("cannot resume an active or invalid job")
        if not (old / "work.ext3").is_file() or (old / "work.ext3").is_symlink():
            raise ValueError("missing file-backed build state")
    script = r / "job.sbatch"
    script.write_text(render_job(args))
    script.chmod(0o700)
    call(["bash", "-n", script])
    result = call(["sbatch", "--parsable", script], capture_output=True)
    job = result.stdout.strip().split(";")[0]
    if not job.isdigit():
        raise ValueError("invalid sbatch response")
    (r / "job.id").write_text(job + "\n")
    (r / "request.json").write_text(json.dumps(dict(vars(args), controller=str(CONTROL)), sort_keys=True) + "\n")
    print(job, flush=True)

def monitor(args):
    r = task_dir(args.run_id)
    job = (r / "job.id").read_text().strip()
    if not job.isdigit():
        raise ValueError("invalid job id")
    deadline = time.monotonic() + args.timeout
    previous = None
    accounting_misses = 0
    while time.monotonic() < deadline:
        queued = call(["squeue", "-h", "-j", job, "-o", "%T|%R"], capture_output=True).stdout.strip()
        if queued:
            accounting_misses = 0
            if queued != previous:
                print(f"{job}: {queued}", flush=True)
                previous = queued
        else:
            rows = call(["sacct", "-X", "-n", "-P", "-j", job,
                         "-o", "JobIDRaw,State,ExitCode"], capture_output=True).stdout.splitlines()
            values = next((row.split("|") for row in rows if row.split("|")[0] == job), None)
            if values and values[1] not in ("RUNNING", "PENDING", "COMPLETING"):
                print("|".join(values), flush=True)
                success = values[1] == "COMPLETED" and values[2] == "0:0" and (r / "artifact.path").exists()
                (r / "results/status.json").write_text(json.dumps({"job": job, "state": values[1],
                                                                  "exit_code": values[2], "verified": success}) + "\n")
                if success:
                    request = json.loads((r / "request.json").read_text())
                    artifact = Path((r / "artifact.path").read_text().strip())
                    expected = ROOT / "containers/software" / request["software"] / request["version"] / request["target"] / (args.run_id + ".sif")
                    if artifact != expected or artifact.resolve() != expected:
                        raise ValueError("unexpected published artifact path")
                    manifest = {"software": request["software"], "source_sha": request["sha"],
                                "version": request["version"], "target": request["target"],
                                "artifact": str(artifact), "sha256": checksum(artifact),
                                "controller": request.get("controller", "legacy-unrecorded"), "verified": True}
                    artifact.with_suffix(".json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
                log = r / "results" / f"slurm-{job}.log"
                if log.is_file():
                    print("\n".join(log.read_text(errors="replace").splitlines()[-100:]), flush=True)
                return 0 if success else 1
            accounting_misses += 1
            if accounting_misses > 12:
                raise RuntimeError("Slurm accounting unavailable; job state is unknown")
        time.sleep(args.interval)
    # Do not cancel a job merely because a monitor timed out.
    raise TimeoutError(f"monitor deadline reached; job {job} was not cancelled")

def lookup(args):
    directory = ROOT / "containers/software" / safe_name(args.software) / safe_name(args.version) / safe_name(args.target)
    requested = safe_sha(args.sha)
    for sidecar in sorted(directory.glob("*.json"), reverse=True):
        if sidecar.is_symlink():
            continue
        data = json.loads(sidecar.read_text())
        artifact = sidecar.with_suffix(".sif")
        if (data.get("verified") is True and data.get("source_sha") == requested and
                data.get("target") == args.target and data.get("artifact") == str(artifact) and
                artifact.is_file() and not artifact.is_symlink() and
                checksum(artifact) == data.get("sha256")):
            print(json.dumps(data))
            return
    print("{}")

def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="op", required=True)
    a = sub.add_parser("init")
    a.add_argument("software"); a.add_argument("run_id")
    a = sub.add_parser("submit")
    for field in ("software", "run_id", "sha", "version"):
        a.add_argument(field)
    a.add_argument("target", choices=TARGETS)
    a.add_argument("--jobs", type=int, default=8)
    a.add_argument("--minutes", type=int, default=120)
    a.add_argument("--overlay-mb", type=int, default=8192)
    a.add_argument("--resume-run", help="repack a terminated run's existing overlay; never rebuild source")
    a = sub.add_parser("monitor")
    a.add_argument("run_id"); a.add_argument("--timeout", type=int, default=14400)
    a.add_argument("--interval", type=int, default=15)
    a = sub.add_parser("lookup")
    for field in ("software", "version", "target", "sha"):
        a.add_argument(field)
    args = p.parse_args()
    result = {"init": init, "submit": submit, "monitor": monitor, "lookup": lookup}[args.op](args)
    return result if isinstance(result, int) else 0

if __name__ == "__main__":
    raise SystemExit(main())
