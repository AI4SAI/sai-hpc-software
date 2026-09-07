#!/usr/bin/env python3
"""Lossless Git bundle transport: gzip, eight verified parts, persistent bare cache."""
import argparse
import fcntl
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

PARTS = 8
LIMIT = 4 * 1024**3

def git(repo, *args):
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args],
                          check=True, text=True, capture_output=True).stdout.strip()

def sha(value):
    if not re.fullmatch("[0-9a-f]{40}", value):
        raise ValueError("invalid SHA")
    return value

def checksum(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            h.update(block)
    return h.hexdigest()

def complete(repo, commit):
    sha(commit)
    try:
        git(repo, "rev-list", "--objects", "--missing=error", commit)
        return True
    except subprocess.CalledProcessError:
        return False

def pack(repo, commit, output, base=None):
    repo, output = Path(repo).resolve(), Path(output).resolve()
    commit = sha(git(repo, "rev-parse", commit + "^{commit}"))
    if git(repo, "rev-parse", "--is-shallow-repository") != "false":
        raise ValueError("shallow source is not a self-contained bundle; fetch full history")
    if not complete(repo, commit):
        raise ValueError("incomplete source history")
    if base:
        base = sha(base)
        if not complete(repo, base):
            raise ValueError("base unavailable locally")
        subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", base, commit], check=True)
    output.mkdir(parents=True, exist_ok=True)
    bundle = output / "source.bundle"
    if bundle.exists() or (output / "manifest.json").exists():
        raise ValueError("pack output is not empty")
    ref = f"refs/bundle/{commit}"
    git(repo, "update-ref", ref, commit)
    git(repo, "bundle", "create", str(bundle), ref, *([f"^{base}"] if base else []))
    compressed = output / "source.bundle.gz"
    with bundle.open("rb") as src, compressed.open("wb") as dest:
        with gzip.GzipFile(filename="", fileobj=dest, mode="wb", mtime=0, compresslevel=6) as z:
            shutil.copyfileobj(src, z, 1024**2)
    size = compressed.stat().st_size
    step = (size + PARTS - 1) // PARTS
    parts = []
    with compressed.open("rb") as src:
        for index in range(PARTS):
            path = output / f"source.part.{index:02d}"
            path.write_bytes(src.read(step))
            parts.append({"name": path.name, "size": path.stat().st_size, "sha256": checksum(path)})
    manifest = {"version": 2, "commit": commit, "base": base,
                "bundle_size": bundle.stat().st_size, "bundle_sha256": checksum(bundle),
                "compressed_size": size, "compressed_sha256": checksum(compressed), "parts": parts}
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
    bundle.unlink(); compressed.unlink()
    return manifest

def assemble(directory):
    directory = Path(directory).resolve()
    manifest_path = directory / "manifest.json"
    if manifest_path.is_symlink() or manifest_path.stat().st_size > 16384:
        raise ValueError("unsafe manifest")
    m = json.loads(manifest_path.read_text())
    sha(m["commit"])
    if m["version"] != 2 or len(m["parts"]) != PARTS:
        raise ValueError("unsupported manifest")
    for key in ("bundle_size", "compressed_size"):
        if not isinstance(m[key], int) or not 1 <= m[key] <= LIMIT:
            raise ValueError("size outside policy bounds")
    zpath = directory / "assembled.gz"
    temporary = directory / "assembled.bundle"
    for target in (zpath, temporary, directory / "source.bundle"):
        if target.exists() or target.is_symlink():
            raise ValueError("assembly target already exists")
    with zpath.open("xb") as dest:
        total = 0
        for index, part in enumerate(m["parts"]):
            name = f"source.part.{index:02d}"
            path = directory / name
            if part["name"] != name or path.is_symlink() or not path.is_file():
                raise ValueError("invalid part")
            if path.stat().st_size != part["size"] or checksum(path) != part["sha256"]:
                raise ValueError("part checksum/size mismatch")
            total += part["size"]
            if total > m["compressed_size"]:
                raise ValueError("oversized input")
            with path.open("rb") as src:
                shutil.copyfileobj(src, dest, 1024**2)
    if total != m["compressed_size"] or checksum(zpath) != m["compressed_sha256"]:
        raise ValueError("compressed checksum/size mismatch")
    with gzip.open(zpath, "rb") as src, temporary.open("xb") as dest:
        total = 0
        while block := src.read(1024**2):
            total += len(block)
            if total > m["bundle_size"]:
                raise ValueError("decompressed input too large")
            dest.write(block)
    if total != m["bundle_size"] or checksum(temporary) != m["bundle_sha256"]:
        raise ValueError("bundle checksum/size mismatch")
    result = directory / "source.bundle"
    temporary.rename(result)
    zpath.unlink()
    return m, result

def receive(repo, directory):
    repo = Path(repo).resolve()
    directory = Path(directory).resolve()
    repo.parent.mkdir(parents=True, exist_ok=True)
    # Container never receives this lock, receiver code, or a writable cache.
    with (repo.parent / (repo.name + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not repo.exists():
            subprocess.run(["git", "init", "--bare", str(repo)], check=True, capture_output=True)
        if git(repo, "rev-parse", "--is-bare-repository") != "true":
            raise ValueError("cache must be bare")
        git(repo, "config", "gc.auto", "0")
        git(repo, "config", "fetch.fsckObjects", "true")
        m, bundle = assemble(directory)
        commit = m["commit"]
        git(repo, "bundle", "verify", str(bundle))
        git(repo, "fetch", "--no-tags", str(bundle), f"{commit}:refs/cache/{commit}")
        if not complete(repo, commit):
            raise ValueError("received commit has incomplete ancestry")
        (repo.parent / (repo.name + ".latest")).write_text(commit + "\n")
        # Once the verified objects are in the bare cache, per-run transfer
        # files are disposable. Keeping them would duplicate source storage.
        for path in directory.glob("source.part.*"):
            path.unlink()
        for name in ("manifest.json", "source.bundle", "assembled.gz", "assembled.bundle"):
            path = directory / name
            if path.exists() or path.is_symlink():
                path.unlink()
        return commit

def inventory(repo):
    repo = Path(repo)
    if not repo.exists():
        return {"cache_shas": []}
    tips = git(repo, "for-each-ref", "--sort=-creatordate", "--count=12",
               "--format=%(objectname)", "refs/cache", "refs/tags").splitlines()
    result = []
    for tip in tips:
        commit = git(repo, "rev-parse", tip + "^{commit}")
        if commit not in result and complete(repo, commit):
            result.append(commit)
    return {"cache_shas": result}

def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="op", required=True)
    a = sub.add_parser("pack")
    a.add_argument("repo"); a.add_argument("commit"); a.add_argument("output"); a.add_argument("--base")
    a = sub.add_parser("assemble"); a.add_argument("directory")
    a = sub.add_parser("receive"); a.add_argument("repo"); a.add_argument("directory")
    a = sub.add_parser("inventory"); a.add_argument("repo")
    args = p.parse_args()
    if args.op == "pack":
        print(json.dumps(pack(args.repo, args.commit, args.output, args.base)))
    elif args.op == "assemble":
        print(assemble(args.directory)[1])
    elif args.op == "receive":
        print(receive(args.repo, args.directory))
    else:
        print(json.dumps(inventory(args.repo)))

if __name__ == "__main__":
    main()
