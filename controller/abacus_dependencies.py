#!/usr/bin/env python3
"""Content-pinned site dependencies; extraction is only allowed in the overlay."""
import argparse
import fcntl
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
import tempfile
from urllib.request import urlopen
import zipfile
from source_cache import checksum

LOCK = Path(__file__).with_name("abacus_dependency_lock.json")
ARCHIVE_CACHE = "cache/abacus-dependencies"
CACHE_MOUNT = "/input/abacus-updates"


def load_lock():
    return json.loads(LOCK.read_text())


def dependency_binds(root):
    """Bind only locked archive directories, never expanded host dependencies."""
    lock = load_lock()
    binds = [(Path(lock["site_archive_root"]), "/input/abacus-dependencies")]
    if any("url" in item for item in lock["archives"]):
        binds.append((Path(root) / ARCHIVE_CACHE, CACHE_MOUNT))
    return tuple(binds)


def cache_archives(cache, lock=None, *, source=None):
    """Cache pinned updates from runner downloads or uploaded, unexpanded files."""
    items = [item for item in (load_lock() if lock is None else lock)["archives"] if "url" in item]
    if not items:
        return
    cache = Path(cache)
    if not cache.is_absolute() or cache.resolve() != cache or cache.is_symlink():
        raise ValueError("dependency cache must be an absolute non-symlink directory")
    cache.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(cache / ".archives.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        for item in items:
            if Path(item["file"]).name != item["file"] or item["file"] in (".", "..", ".archives.lock"):
                raise ValueError("dependency cache filename must be a basename")
            archive = cache / item["file"]
            if archive.exists() or archive.is_symlink():
                if archive.is_symlink() or not archive.is_file() or checksum(archive) != item["sha256"]:
                    raise ValueError(f"changed cached dependency: {item['file']}")
                continue
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=cache, prefix=".download-", delete=False) as output:
                    temporary = Path(output.name)
                    if source is None:
                        incoming = urlopen(item["url"], timeout=120)
                    else:
                        uploaded = Path(source) / item["file"]
                        if not uploaded.is_file() or uploaded.is_symlink() or uploaded.resolve() != uploaded:
                            raise ValueError(f"missing regular uploaded dependency: {item['file']}")
                        incoming = uploaded.open("rb")
                    with incoming:
                        shutil.copyfileobj(incoming, output)
                if checksum(temporary) != item["sha256"]:
                    raise ValueError(f"incoming dependency checksum mismatch: {item['file']}")
                temporary.chmod(0o444)
                os.replace(temporary, archive)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)


def validate_members(files, root, required_files=()):
    """Validate member paths and required nonempty files using one archive index."""
    for name in files:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != root:
            raise ValueError("dependency archive has an unsafe or unexpected path")
    for relative in required_files:
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("required dependency file must stay inside its archive root")
        if not files.get(f"{root}/{path}", False):
            raise ValueError(f"missing nonempty regular dependency file: {root}/{path}")


def extract_archive(archive, destination, root, required_files=()):
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as stream:
            entries = stream.infolist()
            if any(stat.S_ISLNK(entry.external_attr >> 16) for entry in entries):
                raise ValueError("dependency zip links are not allowed")
            validate_members({entry.filename: not entry.is_dir() and entry.file_size > 0 and
                              stat.S_IFMT(entry.external_attr >> 16) in (0, stat.S_IFREG)
                              for entry in entries}, root, required_files)
            stream.extractall(destination)
            # zipfile deliberately does not restore Unix executable modes.
            # Torch's packaged helpers must remain usable in the exported tree.
            for entry in entries:
                permissions = (entry.external_attr >> 16) & 0o777
                if permissions:
                    (destination / entry.filename).chmod(permissions)
    else:
        with tarfile.open(archive, "r:gz") as stream:
            entries = stream.getmembers()
            if any(not (entry.isfile() or entry.isdir()) for entry in entries):
                raise ValueError("dependency tar links and special files are not allowed")
            validate_members({entry.name: entry.isfile() and entry.size > 0 for entry in entries},
                             root, required_files)
            stream.extractall(destination, members=entries)


def unpack(source, destination, lock):
    source, destination = Path(source), Path(destination)
    if not destination.is_absolute() or not destination.is_relative_to("/workspace"):
        raise ValueError("dependency extraction must stay inside /workspace in the build overlay")
    if destination.exists() or destination.is_symlink() or destination.resolve() != destination:
        raise ValueError("dependency destination already exists")
    # Validate every archive before creating the expanded dependency tree.
    archives = [(item, (Path(CACHE_MOUNT) if "url" in item else source) / item["file"])
                for item in lock["archives"]]
    for item, path in archives:
        if not path.is_file() or path.is_symlink() or checksum(path) != item["sha256"]:
            raise ValueError(f"missing or changed pinned dependency: {item['file']}")
    destination.mkdir(parents=True)
    for item, archive in archives:
        extract_archive(archive, destination, item["root"], item.get("required_files", ()))
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
