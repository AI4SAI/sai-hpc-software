#!/usr/bin/env python3
"""GitHub-side orchestration; remote commands always come from this repository."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from remote_controller import safe_name, safe_sha, TARGETS
from source_cache import pack

def run(argv, **kwargs):
    return subprocess.run([str(x) for x in argv], check=True, text=True, **kwargs)

def main():
    software = os.environ.get("SOFTWARE", "abacus")
    if software != "abacus":
        raise ValueError("unknown software recipe")
    target = os.environ["TARGET"]
    if target not in TARGETS:
        raise ValueError("unknown target")
    upstream = safe_sha(os.environ["SOURCE_SHA"])
    control_sha = safe_sha(os.environ["GITHUB_SHA"])
    version = safe_name(os.environ["SOFTWARE_VERSION"])
    user = safe_name(os.environ["REMOTE_USER"])
    run_id = safe_name(os.environ["GITHUB_RUN_ID"] + "-" + os.environ["GITHUB_RUN_ATTEMPT"] + "-" + target)
    temporary = Path(os.environ["RUNNER_TEMP"])
    key = temporary / "ssh/key"
    known_hosts = Path(__file__).resolve().parents[1] / ".ci/slurm/known_hosts"
    options = ["-i", str(key), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", f"UserKnownHostsFile={known_hosts}", "-o", "ConnectTimeout=20",
               "-o", "ServerAliveInterval=20", "-o", "ServerAliveCountMax=6"]
    remote = f"{user}@c0.sai.ai-4s.com"
    root = f"/home/{user}/sai-hpc-software"
    control = f"{root}/controller/{control_sha}"
    task = f"{root}/runs/{run_id}"
    cache = f"{root}/cache/repositories/{software}"
    def ssh(argv, **kw):
        return run(["ssh", *options, "-p", "12022", remote, shlex.join(argv)], **kw)
    def python(script, *args, **kw):
        return ssh(["python3", f"{control}/{script}", *args], **kw)
    def upload(source, destination):
        for attempt in range(3):
            try:
                return run(["scp", "-q", *options, "-P", "12022", source,
                            f"{remote}:{destination}"], timeout=1800)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                if attempt == 2:
                    raise
                print(f"retrying part {Path(source).name}", flush=True)
                time.sleep(3)
    ssh(["mkdir", "-p", control, f"{task}/input", f"{task}/results"])
    parent = Path(__file__).resolve().parent
    for name in ("software_controller.py", "remote_controller.py", "source_cache.py",
                 "container_entry.sh", "create_rootfs.sh", "environment.sh", "abacus_build.sh"):
        upload(parent / name, f"{control}/{name}")
    inventory = json.loads(python("source_cache.py", "inventory", cache, capture_output=True).stdout)
    if upstream in inventory["cache_shas"]:
        print(f"CACHE_HIT {upstream}: zero source upload", flush=True)
    else:
        repo = temporary / "source.git"
        run(["git", "clone", "--bare", "https://github.com/deepmodeling/abacus-develop.git", repo])
        run(["git", "-C", repo, "fetch", "--no-tags", "origin", upstream])
        base = None
        for candidate in inventory["cache_shas"]:
            result = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", candidate, upstream],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if result.returncode == 0 and candidate != upstream:
                base = candidate
                break
        parts = temporary / "parts"
        manifest = pack(repo, upstream, parts, base)
        print(f"SOURCE_TRANSFER base={base} compressed_bytes={manifest['compressed_size']} parts=8", flush=True)
        upload(parts / "manifest.json", f"{task}/input/manifest.json")
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(upload, parts / f"source.part.{i:02d}",
                                       f"{task}/input/source.part.{i:02d}") for i in range(8)]
            for future in futures:
                future.result()
        python("source_cache.py", "receive", cache, f"{task}/input")
    # Fail before scheduling if the manually provisioned minimal base is missing.
    ssh(["test", "-s", f"{root}/containers/base/minimal-v1.sif"])
    python("software_controller.py", "submit", software, run_id, upstream, version, target)
    results = temporary / "results"
    results.mkdir(exist_ok=True)
    try:
        python("software_controller.py", "monitor", run_id)
        run(["scp", "-q", *options, "-P", "12022", f"{remote}:{task}/artifact.path", results / "artifact.path"])
        print((results / "artifact.path").read_text(), flush=True)
    finally:
        # Only logs/metadata travel back; the single SIF stays in the SAI catalog.
        subprocess.run(["scp", "-q", *options, "-P", "12022", "-r",
                        f"{remote}:{task}/results/.", str(results)], check=False)

if __name__ == "__main__":
    main()
