#!/usr/bin/env python3
"""Generate the pinned, offline Spack configuration used by CP2K.

This module deliberately only describes the dependency resolver.  It does not
silently turn the existing CP2K toolchain into Spack externals: every external
path is explicit and the install/cache roots are partition-specific.  The
generated files can therefore be copied into a container overlay or inspected
before a native build is started.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

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
    root = cache.resolve()
    return {
        "root": root,
        "sources": root / "sources",
        "buildcache": root / "buildcache" / partition,
        "store": root / "store" / partition,
        "config": root / "config" / partition,
    }


def validate_cache(cache: Path, partition: str, *, require_archives: bool = False) -> dict[str, str]:
    roots = cache_roots(cache, partition)
    for key in ("sources", "buildcache", "store", "config"):
        path = roots[key]
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ValueError(f"Spack cache path is not a directory: {path}")
    if require_archives:
        for name, digest in (("spack.tar.gz", SPACK_SHA256), ("packages.tar.gz", PACKAGES_SHA256)):
            archive = roots["sources"] / name
            if not archive.is_file():
                raise ValueError(f"missing pinned Spack archive: {archive}")
            if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
                raise ValueError(f"checksum mismatch: {archive}")
    return {key: str(value) for key, value in roots.items()}


def config(partition: str, cache: Path, *, spack_root: Path, package_repo: Path) -> dict:
    roots = cache_roots(cache, partition)
    target = TARGETS[partition]
    compiler = target["compiler"]
    return {
        "spack_commit": SPACK_COMMIT,
        "packages_commit": PACKAGES_COMMIT,
        "partition": partition,
        "repos": {"sai-pinned": str(package_repo.resolve())},
        "config": {"install_tree": {"root": str(roots["store"]), "projections": {
            "all": "{architecture.platform}-{architecture.target}/{name}-{version}-{hash}"
        }}, "source_cache": str(roots["sources"]), "build_stage": [str(roots["root"] / "stage")]},
        "mirror": {"source": f"file://{roots['sources']}", "binary": f"file://{roots['buildcache']}"},
        "compiler": {"spec": "gcc@13.3.0 languages='c,c++,fortran'", "prefix": compiler,
                     "cc": str(Path(compiler) / "bin/gcc"), "cxx": str(Path(compiler) / "bin/g++"),
                     "fortran": str(Path(compiler) / "bin/gfortran")},
        "spack_root": str(spack_root.resolve()),
        "cuda": target["cuda"],
        "isa": target["isa"],
    }


def write_config(output: Path, value: dict) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("partition", choices=sorted(TARGETS))
    parser.add_argument("cache", type=Path)
    parser.add_argument("--spack-root", type=Path, required=True)
    parser.add_argument("--package-repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-archives", action="store_true")
    args = parser.parse_args()
    validate_cache(args.cache, args.partition, require_archives=args.require_archives)
    write_config(args.output, config(args.partition, args.cache,
                                     spack_root=args.spack_root,
                                     package_repo=args.package_repo))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
