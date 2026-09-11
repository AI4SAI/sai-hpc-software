#!/usr/bin/env python3
"""Resolve the two moving upstreams as one immutable, ABI-tested build unit."""
import argparse
import hashlib
import json
from pathlib import Path
from remote_controller import safe_name, safe_sha
from resolve_source import resolve

REPOSITORIES = {"deepmd-kit": "deepmodeling/deepmd-kit", "lammps": "lammps/lammps"}
TARGETS = ("4v100-avx512", "16v100-avx2", "8v100v0-avx512")


def pair(deepmd_ref, lammps_ref, target, resolver=resolve):
    if target not in TARGETS:
        raise ValueError("MD target has no feature/ABI acceptance policy")
    sources = {name: resolver(repository, ref) for name, repository, ref in (
        ("deepmd-kit", REPOSITORIES["deepmd-kit"], deepmd_ref),
        ("lammps", REPOSITORIES["lammps"], lammps_ref))}
    for name, source in sources.items():
        safe_sha(source["sha"])
        safe_name(source["version"])
        source["repository"] = REPOSITORIES[name]
    label = "dp-" + sources["deepmd-kit"]["sha"][:12] + "-lmp-" + sources["lammps"]["sha"][:12]
    return {"schema": 1, "version": label, "target": target, "sources": sources}


def fingerprint(control=None):
    control = Path(control or __file__).resolve()
    if control.is_file():
        control = control.parent
    names = [*sorted(p.name for p in control.glob("md_*.*") if p.suffix in (".py", ".sh", ".json")),
             "remote_controller.py", "source_cache.py", "create_rootfs.sh", "resolve_source.py"]
    digest = hashlib.sha256(b"sai-deepmd-lammps-contract-v1\n")
    for name in names:
        path = control / name
        if not path.is_file() or path.is_symlink():
            raise ValueError("untrusted MD controller file")
        digest.update(name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--deepmd-ref", default="master")
    parser.add_argument("--lammps-ref", default="develop")
    parser.add_argument("--target", choices=TARGETS, default=TARGETS[0])
    args = parser.parse_args()
    print(json.dumps(pair(args.deepmd_ref, args.lammps_ref, args.target), sort_keys=True))
