#!/usr/bin/env python3
"""Submit a walltime-only LAMMPS continuation from a frozen overlay.

This helper is deliberately outside the r4 controller recipe.  It copies the
terminated run's file-backed overlay into a fresh run, verifies the source did
not change while copying, and changes only the run id, dependency bookkeeping,
and walltime.  The old run and overlay are never opened writable.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb", buffering=0) as stream:
        for chunk in iter(lambda: stream.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(argv, **kwargs):
    return subprocess.run([str(x) for x in argv], check=True, text=True, **kwargs)


def safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError("unsafe run id")
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("old_run")
    parser.add_argument("new_run")
    parser.add_argument("--minutes", type=int, default=720)
    args = parser.parse_args()
    safe_id(args.old_run)
    safe_id(args.new_run)
    if args.old_run == args.new_run or not 1 <= args.minutes <= 1440:
        raise ValueError("invalid continuation arguments")

    project = Path(os.environ.get("SAI_SOFTWARE_ROOT", Path.home() / "sai-hpc-software")).resolve()
    root = project / "experimental/deepmd-lammps"
    old = root / "runs" / args.old_run
    new = root / "runs" / args.new_run
    for path in (old, new):
        if path.resolve() != path or path.is_symlink():
            raise ValueError("run path must be a non-symlink absolute path")
    if not old.is_dir() or new.exists():
        raise ValueError("old run missing or new run already exists")
    status = json.loads((old / "results/status.json").read_text())
    if status.get("state") != "TIMEOUT":
        raise ValueError("continuation is restricted to the diagnosed timeout")
    old_job = (old / "job.id").read_text().strip()
    if not old_job.isdigit():
        raise ValueError("invalid old job receipt")
    queue = subprocess.run(["squeue", "-h", "-j", old_job, "-o", "%i"],
                           check=False, text=True, capture_output=True)
    if queue.returncode == 0 and queue.stdout.strip():
        raise ValueError("old LAMMPS job is still active")
    overlay = old / "work.ext3"
    if not overlay.is_file() or overlay.is_symlink():
        raise ValueError("old overlay is not a regular file")
    before = sha256(overlay)

    new.mkdir(mode=0o700)
    for part in ("input", "results", "runtime", "apptainer-cache"):
        (new / part).mkdir(mode=0o700)
    # Reflink when the filesystem supports it; otherwise retain a byte-for-byte
    # copy.  No ownership or mode from an unrelated account is imported.
    run(["cp", "--reflink=auto", "--sparse=always", "--preserve=mode,timestamps",
         overlay, new / "work.ext3"])
    after_source = sha256(overlay)
    copied = sha256(new / "work.ext3")
    if before != after_source or before != copied:
        raise RuntimeError("overlay changed during copy or failed hash verification")

    for name in ("input/pair.json", "recovery-authorization.json", "recovery-preflight.json"):
        source = old / name
        if source.is_file() and not source.is_symlink():
            shutil.copy2(source, new / name)
    old_script = (old / "job.sbatch").read_text()
    script = old_script.replace(args.old_run, args.new_run)
    script = script.replace("#SBATCH --dependency=afterok:1440200\n", "")
    script = script.replace("#SBATCH --kill-on-invalid-dep=yes\n", "")
    if script.count("#SBATCH --time=330") != 1:
        raise ValueError("unexpected old LAMMPS walltime")
    script = script.replace("#SBATCH --time=330", f"#SBATCH --time={args.minutes}")
    if args.old_run in script or "--dependency=afterok:" in script or "--kill-on-invalid-dep" in script:
        raise ValueError("old run/dependency leaked into continuation script")
    (new / "job.sbatch").write_text(script)
    (new / "job.sbatch").chmod(0o700)
    run(["bash", "-n", new / "job.sbatch"])
    run(["sbatch", "--test-only", new / "job.sbatch"], capture_output=True)
    job = run(["sbatch", "--parsable", new / "job.sbatch"], capture_output=True).stdout.strip().split(";")[0]
    if not job.isdigit():
        raise ValueError("invalid continuation job receipt")

    request = json.loads((old / "request.json").read_text())
    request["run_id"] = args.new_run
    request["stage_jobs"] = dict(request.get("stage_jobs", {}), lammps=job)
    request["job"] = job
    script_sha = hashlib.sha256((new / "job.sbatch").read_bytes()).hexdigest()
    request["job_script_sha256"] = script_sha
    request["stage_job_script_sha256"] = dict(request.get("stage_job_script_sha256", {}), lammps=script_sha)
    (new / "request.json").write_text(json.dumps(request, sort_keys=True) + "\n")
    (new / "job.id").write_text(job + "\n")
    (new / "recovery-preflight.json").write_text(json.dumps({
        "source_run": args.old_run, "new_run": args.new_run, "source_overlay_sha256": before,
        "copied_overlay_sha256": copied, "walltime_minutes": args.minutes,
        "qos": "rush-gpu", "gpus": 4, "build_jobs": 24,
        "delivery_identity_unchanged": True, "source_overlay_rechecked": before == after_source,
        "job": job, "job_script_sha256": script_sha,
    }, sort_keys=True) + "\n")
    print(json.dumps({"job": job, "run": str(new), "source_overlay_sha256": before,
                      "copied_overlay_sha256": copied, "minutes": args.minutes}, sort_keys=True))


if __name__ == "__main__":
    main()
