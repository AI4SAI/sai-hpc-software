"""Canonical identity-aware artifact paths shared by build and runtime gates."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import stat

from release_contract import validate_identity
from remote_controller import safe_name
from source_cache import checksum

CONTRACT_SCHEMA = 3


def catalog_dir(root, identity):
    identity = validate_identity(identity)
    root = Path(root)
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError("delivery root must be an absolute path without symlinks")
    result = root / "containers/software" / identity["software"] / identity["track"] / identity["build_id"] / identity["partition"]
    if result.resolve() != result:
        raise ValueError("delivery catalog must not contain symlinks")
    return result


def artifact_path(root, identity, run_id):
    return catalog_dir(root, identity) / (safe_name(run_id) + ".sif")


def validate_record(record):
    """Requests/sidecars must agree with every duplicated provenance field."""
    if not isinstance(record, dict):
        raise ValueError("delivery record must be an object")
    identity = validate_identity(record.get("identity"))
    required = {"software": identity["software"], "version": identity["source_version"],
                "target": identity["target"], "recipe_sha256": identity["recipe_sha256"]}
    if any(record.get(key) != value for key, value in required.items()):
        raise ValueError("delivery record differs from its identity")
    source_keys = set(record) & {"sha", "source_sha"}
    if not source_keys or any(record[key] != identity["source_sha"] for key in source_keys):
        raise ValueError("delivery source differs from its identity")
    for key in ("track", "source_ref"):
        if key in record and record[key] != identity[key]:
            raise ValueError("delivery track/reference differs from its identity")
    return identity


def _artifact_record(root, artifact, *, software=None, target=None):
    artifact = Path(artifact)
    sidecar = artifact.with_suffix(".json")
    if (not artifact.is_file() or artifact.resolve() != artifact or artifact.is_symlink() or
            not sidecar.is_file() or sidecar.resolve() != sidecar or sidecar.is_symlink()):
        raise ValueError("delivery requires regular pinned artifact and sidecar")
    sidecar_bytes = sidecar.read_bytes()
    record = json.loads(sidecar_bytes)
    identity = validate_record(record)
    if (artifact != artifact_path(root, identity, artifact.stem) or
            record.get("artifact") != str(artifact) or record.get("build_verified") is not True or
            record.get("contract_schema") != CONTRACT_SCHEMA or
            (software is not None and identity["software"] != software) or
            (target is not None and identity["target"] != target)):
        raise ValueError("artifact does not match its immutable delivery identity")
    return record, hashlib.sha256(sidecar_bytes).hexdigest()


def load_artifact(root, artifact, *, software=None, target=None):
    """Always hash the full image; this is not scientific acceptance."""
    record, _ = _artifact_record(root, artifact, software=software, target=target)
    if record.get("sha256") != checksum(artifact):
        raise ValueError("artifact does not match its immutable delivery identity")
    return record


def _artifact_stats(artifact):
    result = []
    for path in (artifact, artifact.with_suffix(".json")):
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or path.resolve() != path or path.is_symlink():
            raise ValueError("delivery requires regular pinned artifact and sidecar")
        result.append([info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns])
    return result


def load_runtime_artifact(root, artifact, *, software=None, target=None):
    """Reuse a full checksum only within this Slurm job on this host.

    Every rank still checks canonical metadata and both files' current stats.
    The record is also the lock: keep its inode stable and do not unlink it
    from rank cleanup. Interrupted writes are cache misses on the next call.
    """
    job = os.environ.get("SLURM_JOB_ID")
    if not job:
        return load_artifact(root, artifact, software=software, target=target)
    job = safe_name(job)
    artifact = Path(artifact)
    # Validate the delivery root and identity before creating cache files.
    _artifact_record(root, artifact, software=software, target=target)
    hostname = socket.gethostname()
    key = hashlib.sha256((hostname + "\0" + str(artifact)).encode()).hexdigest()
    directory = Path(root) / "runtime/jobs" / job
    cache_path = directory / ("artifact-check-" + key + ".json")
    if cache_path.resolve() != cache_path or cache_path.is_symlink():
        raise ValueError("runtime artifact cache must not contain symlinks")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(cache_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    info = os.fstat(descriptor)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
            info.st_nlink != 1 or info.st_mode & 0o022):
        os.close(descriptor)
        raise ValueError("runtime artifact cache must be an owned, private regular file")
    with os.fdopen(descriptor, "r+", encoding="utf-8") as cache:
        fcntl.flock(cache, fcntl.LOCK_EX)
        # Take the snapshot after acquiring the lock, not before waiting for it.
        before = _artifact_stats(artifact)
        record, sidecar_sha256 = _artifact_record(root, artifact, software=software, target=target)
        verified = dict(schema=1, job=job, hostname=hostname, artifact=str(artifact),
                        job_start=os.environ.get("SLURM_JOB_START_TIME", ""),
                        restart_count=os.environ.get("SLURM_RESTART_COUNT", ""),
                        sha256=record.get("sha256"), artifact_stat=before[0],
                        sidecar_stat=before[1], sidecar_sha256=sidecar_sha256)
        try:
            cached = json.load(cache)
        except (ValueError, UnicodeError):
            cached = None
        if cached != verified and checksum(artifact) != record.get("sha256"):
            raise ValueError("artifact does not match its immutable delivery identity")
        if before != _artifact_stats(artifact):
            raise ValueError("artifact or sidecar changed during runtime validation")
        if cached != verified:
            cache.seek(0)
            cache.truncate()
            json.dump(verified, cache, sort_keys=True)
            cache.write("\n")
            cache.flush()
        return record


def validate_runtime_identity(root, request):
    identity = validate_identity(request.get("identity"))
    if (identity["software"] != "abacus" or request.get("target") != identity["target"] or
            request.get("version") != identity["source_version"] or
            Path(request["artifact"]) != artifact_path(root, identity, request["build_run_id"])):
        raise ValueError("runtime request differs from its canonical identity")
    record = load_artifact(root, request["artifact"], software="abacus", target=request["target"])
    if record["identity"] != identity or record["sha256"] != request["artifact_sha256"]:
        raise ValueError("runtime request differs from its pinned artifact identity")
    return identity


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    runtime = commands.add_parser("runtime")
    for field in ("root", "artifact", "software", "target"):
        runtime.add_argument(field)
    prefix = commands.add_parser("prefix")
    for field in ("identity", "software", "sha", "version", "target"):
        prefix.add_argument(field)
    prefix.add_argument("--installed", type=Path)
    args = parser.parse_args()
    if args.command == "runtime":
        record = load_runtime_artifact(args.root, args.artifact, software=args.software, target=args.target)
        print(record["identity"]["install_prefix"])
    else:
        identity = validate_identity(json.loads(args.identity))
        if any(identity[key] != getattr(args, name) for key, name in (
                ("software", "software"), ("source_sha", "sha"),
                ("source_version", "version"), ("target", "target"))):
            raise ValueError("build arguments differ from the canonical identity")
        if args.installed and validate_identity(json.loads(args.installed.read_text())) != identity:
            raise ValueError("installed identity differs from the build request")
        print(identity["install_prefix"])


if __name__ == "__main__":
    main()
