#!/usr/bin/env python3
"""Describe pins and paths for the future CP2K Spack dependency build.

This module deliberately only describes the dependency resolver.  It does not
silently turn the existing CP2K toolchain into Spack externals: every external
path is explicit. This is a preparation manifest, NOT spack.yaml or a concrete
dependency lockfile. No existing build workflow consumes it yet. Install and
staging paths are container paths; the host cache holds archives only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

SPACK_COMMIT = "3e19345b6e12f5ff1b874f4059622fc6a1fd804a"
PACKAGES_COMMIT = "535022224b610cb07dcbf63cef5bd68c6a2aae24"
SPACK_SHA256 = "a4bbe032981461ac166b3acc10f5f38bbb34e804695478de6a2e1d11955f8d58"
PACKAGES_SHA256 = "f6f827ead872ce90c9c382f5a47043d79369c2a29cf18ca769f01fa9b8e65590"

TARGETS = {
    "DSPRHBM": {"isa": "avx512", "cuda": False, "compiler": "/opt/devtools/gcc/13.3.0"},
    "4V100": {"isa": "avx512", "cuda": True, "compiler": "/opt/devtools/gcc/13.3.0"},
    "16V100": {"isa": "avx2", "cuda": True, "compiler": "/opt/devtools/gcc/13.3.0"},
    "8V100V0": {"isa": "avx2", "cuda": True, "compiler": "/opt/devtools/gcc/13.3.0"},
}


def cache_roots(cache: Path, partition: str) -> dict[str, Path]:
    if partition not in TARGETS:
        raise ValueError(f"unsupported CP2K Spack partition: {partition}")
    root = Path(cache)
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError("cache path must be absolute and contain no symlinks or traversal")
    return {
        "root": root,
        "sources": root / "sources",
        "buildcache": root / "buildcache" / partition,
    }


def validate_cache(cache: Path, partition: str, *, require_archives: bool = False) -> dict[str, str]:
    roots = cache_roots(cache, partition)
    for key in ("sources", "buildcache"):
        path = roots[key]
        if path.resolve() != path or (path.exists() and not path.is_dir()):
            raise ValueError(f"Spack cache path is not a directory: {path}")
    if require_archives:
        for name, digest in (("spack.tar.gz", SPACK_SHA256), ("packages.tar.gz", PACKAGES_SHA256)):
            archive = roots["sources"] / name
            if archive.is_symlink() or not archive.is_file():
                raise ValueError(f"missing pinned Spack archive: {archive}")
            checksum = hashlib.sha256()
            with archive.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    checksum.update(block)
            if checksum.hexdigest() != digest:
                raise ValueError(f"checksum mismatch: {archive}")
    return {key: str(value) for key, value in roots.items()}


def config(partition: str, cache: Path, *, install_prefix: Path) -> dict:
    roots = cache_roots(cache, partition)
    target = TARGETS[partition]
    compiler = target["compiler"]
    prefix = str(install_prefix)
    if not re.fullmatch(r"/opt/software/cp2k/(development|prerelease|release)/[A-Za-z0-9][A-Za-z0-9_.-]*/" + partition, prefix):
        raise ValueError("Spack store must be inside this partition's canonical CP2K prefix")
    return {
        "schema": 1,
        "status": "preparation-only-not-concretized",
        "spack_commit": SPACK_COMMIT,
        "packages_commit": PACKAGES_COMMIT,
        "partition": partition,
        "archives": {
            "spack": {"url": "https://api.github.com/repos/spack/spack/tarball/" + SPACK_COMMIT,
                      "sha256": SPACK_SHA256},
            "packages": {"url": "https://api.github.com/repos/spack/spack-packages/tarball/" + PACKAGES_COMMIT,
                         "sha256": PACKAGES_SHA256}},
        "host_cache": {key: str(value) for key, value in roots.items()},
        "container": {
        "repos": {"builtin": "/workspace/spack-packages/repos/spack_repo/builtin"},
        "config": {"install_tree": {"root": prefix + "/dependencies/spack", "projections": {
            "all": "{architecture.platform}-{architecture.target}/{name}-{version}-{hash}"
        }}, "source_cache": "/workspace/spack-source-cache", "build_stage": ["/workspace/spack-stage"]},
        "bootstrap": {"enable": False},
        "spack_root": "/workspace/spack"},
        "compiler": {"spec": "gcc@13.3.0 languages='c,c++,fortran'", "prefix": compiler,
                     "cc": str(Path(compiler) / "bin/gcc"), "cxx": str(Path(compiler) / "bin/g++"),
                     "fortran": str(Path(compiler) / "bin/gfortran")},
        "cuda": target["cuda"],
        "isa": target["isa"],
    }


def write_config(output: Path, value: dict) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("partition", choices=sorted(TARGETS))
    parser.add_argument("cache", type=Path)
    parser.add_argument("--install-prefix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-archives", action="store_true")
    args = parser.parse_args()
    validate_cache(args.cache, args.partition, require_archives=args.require_archives)
    write_config(args.output, config(args.partition, args.cache,
                                     install_prefix=args.install_prefix))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
