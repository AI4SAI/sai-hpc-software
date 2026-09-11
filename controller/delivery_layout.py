"""Canonical identity-aware artifact paths shared by build and runtime gates."""
import argparse
import json
from pathlib import Path

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


def load_artifact(root, artifact, *, software=None, target=None):
    """Reject legacy or relabelled images; this is not scientific acceptance."""
    artifact = Path(artifact)
    sidecar = artifact.with_suffix(".json")
    if (not artifact.is_file() or artifact.resolve() != artifact or artifact.is_symlink() or
            not sidecar.is_file() or sidecar.resolve() != sidecar or sidecar.is_symlink()):
        raise ValueError("delivery requires regular pinned artifact and sidecar")
    record = json.loads(sidecar.read_text())
    identity = validate_record(record)
    if (artifact != artifact_path(root, identity, artifact.stem) or
            record.get("artifact") != str(artifact) or record.get("build_verified") is not True or
            record.get("contract_schema") != CONTRACT_SCHEMA or
            record.get("sha256") != checksum(artifact) or
            (software is not None and identity["software"] != software) or
            (target is not None and identity["target"] != target)):
        raise ValueError("artifact does not match its immutable delivery identity")
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
        record = load_artifact(args.root, args.artifact, software=args.software, target=args.target)
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
