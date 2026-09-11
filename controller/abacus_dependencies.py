#!/usr/bin/env python3
"""Content-pinned site dependencies; extraction is only allowed in the overlay."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import tarfile
import zipfile

LOCK = Path(__file__).with_name("abacus_dependency_lock.json")


def load_lock():
    return json.loads(LOCK.read_text())


def dependency_bind():
    """One precise read-only input mount, never a whole /opt or host install tree."""
    return (Path(load_lock()["site_archive_root"]), "/input/abacus-dependencies")


def checksum(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_members(names, root):
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != root:
            raise ValueError("dependency archive has an unsafe or unexpected path")


def unpack(source, destination, lock):
    source, destination = Path(source), Path(destination)
    if not destination.is_absolute() or not destination.is_relative_to("/workspace"):
        raise ValueError("dependency extraction must stay inside /workspace in the build overlay")
    if destination.exists() or destination.is_symlink():
        raise ValueError("dependency destination already exists")
    # Validate every archive before creating the expanded dependency tree.
    for item in lock["archives"]:
        path = source / item["file"]
        if not path.is_file() or path.is_symlink() or checksum(path) != item["sha256"]:
            raise ValueError(f"missing or changed pinned dependency: {item['file']}")
    destination.mkdir(parents=True)
    for item in lock["archives"]:
        archive = source / item["file"]
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as stream:
                entries = stream.infolist()
                validate_members((entry.filename for entry in entries), item["root"])
                if any(stat.S_ISLNK(entry.external_attr >> 16) for entry in entries):
                    raise ValueError("dependency zip links are not allowed")
                stream.extractall(destination)
        else:
            with tarfile.open(archive, "r:gz") as stream:
                entries = stream.getmembers()
                validate_members((entry.name for entry in entries), item["root"])
                if any(not (entry.isfile() or entry.isdir()) for entry in entries):
                    raise ValueError("dependency tar links and special files are not allowed")
                stream.extractall(destination, members=entries)
    (destination / "dependency-lock.json").write_text(json.dumps(lock, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("unpack",))
    parser.add_argument("--source", default="/input/abacus-dependencies")
    parser.add_argument("--destination", default="/workspace/dependencies")
    args = parser.parse_args()
    unpack(args.source, args.destination, load_lock())


if __name__ == "__main__":
    main()
