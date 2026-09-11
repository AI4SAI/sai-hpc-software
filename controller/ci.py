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
from remote_controller import safe_name, safe_sha
from delivery_layout import artifact_path
from release_contract import SOFTWARE, TRACKS, validate_identity
from source_cache import pack

def run(argv, **kwargs):
    return subprocess.run([str(x) for x in argv], check=True, text=True, **kwargs)

def main():
    software = os.environ.get("SOFTWARE", "abacus")
    if software not in ("abacus", "cp2k"):
        raise ValueError("unknown software recipe")
    target = os.environ["TARGET"]
    if target not in SOFTWARE[software]['targets']:
        raise ValueError("unknown target")
    upstream = safe_sha(os.environ["SOURCE_SHA"])
    control_sha = safe_sha(os.environ["GITHUB_SHA"])
    version = os.environ["SOFTWARE_VERSION"]
    track = os.environ["RELEASE_TRACK"]
    if track not in TRACKS:
        raise ValueError("unknown release track")
    source_ref = os.environ["SOURCE_REF"]
    provenance_flags = ["--track", track, "--source-ref", source_ref]
    user = safe_name(os.environ["REMOTE_USER"])
    run_id = safe_name("-".join((os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"],
                                 track, target, upstream[:12])))
    temporary = Path(os.environ["RUNNER_TEMP"])
    key = temporary / "ssh/key"
    known_hosts = Path(__file__).resolve().parents[1] / ".ci/slurm/known_hosts"
    options = ["-i", str(key), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", f"UserKnownHostsFile={known_hosts}", "-o", "ConnectTimeout=20",
               "-o", "ServerAliveInterval=20", "-o", "ServerAliveCountMax=6"]
    remote = f"{user}@c0.sai.ai-4s.com"
    root = f"/home/{user}/sai-hpc-software"
    # One deployment per run: concurrent matrix uploads cannot truncate code
    # already being read by another running container.
    control = f"{root}/controller/{control_sha}/{run_id}"
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
    common = ["software_controller.py", "remote_controller.py", "source_cache.py", "module_publication.py",
              "runtime_controller.py", "create_rootfs.sh", "release_contract.py", "resolve_source.py",
              "delivery_layout.py"]
    recipe = (["container_entry.sh", "environment.sh", "abacus_build.sh",
               "gpu_feature_controller.py", "gpu_feature_runtime.sh"]
              if software == "abacus" else
              ["cp2k_container_entry.sh", "environment.sh", "cp2k_build.sh"])
    for name in common + recipe:
        upload(parent / name, f"{control}/{name}")
    launcher_name = "abacus" if software == "abacus" else "cp2k"
    upload(parent / ("abacus_runtime.sh" if software == "abacus" else "cp2k_runtime.sh"),
           f"{control}/{launcher_name}")
    ssh(["chmod", "0555", f"{control}/{launcher_name}"])
    results = temporary / "results"
    results.mkdir(exist_ok=True)
    # The uploaded, immutable remote recipe owns the fingerprint. Never invent
    # an identity from the runner's checkout or from a mutable channel label.
    identity = validate_identity(json.loads(python("software_controller.py", "identity", software,
        upstream, version, target, *provenance_flags, capture_output=True).stdout))
    expected_fields = {"software": software, "track": track, "source_ref": source_ref,
                       "source_sha": upstream, "source_version": version, "target": target}
    if any(identity[name] != value for name, value in expected_fields.items()):
        raise ValueError("remote identity differs from the resolved source")
    expected_artifact = artifact_path(Path(root), identity, run_id)
    (results / "identity.json").write_text(json.dumps(identity, sort_keys=True) + "\n")
    # Scheduled trackers reuse only verified, checksum-matching artifacts.
    # Manual dispatch deliberately rebuilds, to allow acceptance and recipe changes.
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule":
        prior = json.loads(python("software_controller.py", "lookup", software, version, target,
                                  upstream, *provenance_flags, capture_output=True).stdout)
        if prior:
            (results / "artifact.path").write_text(prior["artifact"] + "\n")
            (results / "cache-hit.json").write_text(json.dumps(prior) + "\n")
            print(f"ARTIFACT_HIT {prior['artifact']}", flush=True)
            return
    inventory = json.loads(python("source_cache.py", "inventory", cache, capture_output=True).stdout)
    if upstream in inventory["cache_shas"]:
        print(f"CACHE_HIT {upstream}: zero source upload", flush=True)
    else:
        repo = temporary / "source.git"
        repository = ("https://github.com/deepmodeling/abacus-develop.git"
                      if software == "abacus" else "https://github.com/cp2k/cp2k.git")
        run(["git", "clone", "--bare", repository, repo])
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
    resume = os.environ.get("RESUME_RUN", "")
    extras = ["--resume-run", safe_name(resume)] if resume else []
    python("software_controller.py", "submit", software, run_id, upstream, version, target,
           *provenance_flags, *extras)
    try:
        python("software_controller.py", "monitor", run_id)
        if software == "abacus" and target in ("dsprhbm", "4v100-avx512", "16v100-avx2", "8v100v0-avx512"):
            runtime_run = safe_name(run_id + "-multinode")
            python("runtime_controller.py", "submit", runtime_run, version, target,
                   "--build-run-id", run_id)
            python("runtime_controller.py", "monitor", runtime_run)
        if software == "abacus" and target in ("4v100-avx512", "16v100-avx2", "8v100v0-avx512"):
            feature_run = safe_name(run_id + "-gpu-features")
            python("gpu_feature_controller.py", "submit", feature_run, version, target,
                   "--build-run-id", run_id)
            python("gpu_feature_controller.py", "monitor", feature_run)
        python("software_controller.py", "publish", run_id)
        run(["scp", "-q", *options, "-P", "12022", f"{remote}:{task}/artifact.path", results / "artifact.path"])
        if (results / "artifact.path").read_text().strip() != str(expected_artifact):
            raise ValueError("published artifact differs from the delivery identity layout")
        print((results / "artifact.path").read_text(), flush=True)
    finally:
        # Only logs/metadata travel back; the single SIF stays in the SAI catalog.
        subprocess.run(["scp", "-q", *options, "-P", "12022", "-r",
                        f"{remote}:{task}/results/.", str(results)], check=False)
        if software == "abacus" and target in ("dsprhbm", "4v100-avx512", "16v100-avx2", "8v100v0-avx512"):
            runtime_results = results / "runtime"
            runtime_results.mkdir(exist_ok=True)
            runtime_run = safe_name(run_id + "-multinode")
            subprocess.run(["scp", "-q", *options, "-P", "12022", "-r",
                            f"{remote}:{root}/runtime-tests/{runtime_run}/results/.",
                            str(runtime_results)], check=False)
        if software == "abacus" and target in ("4v100-avx512", "16v100-avx2", "8v100v0-avx512"):
            feature_results = results / "gpu-features"
            feature_results.mkdir(exist_ok=True)
            feature_run = safe_name(run_id + "-gpu-features")
            subprocess.run(["scp", "-q", *options, "-P", "12022", "-r",
                            f"{remote}:{root}/runtime-tests/{feature_run}/results/.",
                            str(feature_results)], check=False)

if __name__ == "__main__":
    main()
