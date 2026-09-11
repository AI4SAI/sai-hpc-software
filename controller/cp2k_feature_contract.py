#!/usr/bin/env python3
"""Fail-closed CP2K artifact checks; scientific acceptance is a separate gate."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

BASE_FLAGS = frozenset("omp libint fftw3 libxc elpa parallel scalapack mpi_f08 cosma plumed2 spglib libdftd4 mctc-lib tblite libvori libbqb hdf5".split())
GPU_FLAGS = frozenset("dbcsr_acc offload_cuda cusolvermp cusolvermp_nccl".split())
REQUIRED_OPTIONS = tuple("MPI MPI_F08 FFTW3 LIBXC LIBINT2 ELPA COSMA LIBXS LIBXSMM PLUMED SPGLIB VORI HDF5 DFTD4 TBLITE".split())
TRANSIENT = re.compile(r"/(?:workspace|control|input)(?:/|$)")


def version_flags(text):
    rows = re.findall(r"^\s*cp2kflags:\s*(.+)$", text, re.MULTILINE)
    if len(rows) != 1:
        raise ValueError("missing or ambiguous CP2K flags")
    return set(rows[0].split())


def check_flags(text, target):
    flags = version_flags(text)
    missing = set((BASE_FLAGS | (GPU_FLAGS if target != "dsprhbm" else set())) - flags)
    # CP2K 2026.2 split the old xsmm flag into LIBXS and LIBXSMM.
    if not {"libxs", "libxsmm"}.issubset(flags):
        missing.add("libxs+libxsmm")
    if missing:
        raise ValueError("CP2K feature loss: " + ", ".join(sorted(missing)))
    if target == "dsprhbm" and flags & GPU_FLAGS:
        raise ValueError("CPU artifact unexpectedly requires GPU offload")
    return sorted(flags)


def check_cache(text, prefix, target):
    cache = dict(re.findall(r"^([^#/:][^:=]*):[^=]+=(.*)$", text, re.MULTILINE))
    for option in REQUIRED_OPTIONS:
        if cache.get("CP2K_USE_" + option) not in ("ON", "TRUE", "1"):
            raise ValueError("required CP2K option is disabled: " + option)
    if cache.get("CMAKE_INSTALL_PREFIX") != str(prefix):
        raise ValueError("CP2K installation prefix changed")
    for language in ("C", "CXX", "Fortran"):
        if "-march=native" not in cache.get(f"CMAKE_{language}_FLAGS", "").split():
            raise ValueError("CP2K must be compiled natively on each target")
    if cache.get("CP2K_USE_ACCEL") != ("NONE" if target == "dsprhbm" else "CUDA"):
        raise ValueError("wrong CP2K accelerator backend")
    elpa = cache.get("CP2K_ELPA_ROOT", "")
    if not elpa.startswith("/opt/devtools/elpa/elpa-2026.02.001-2603-gnu/"):
        raise ValueError("ELPA must use the pinned 2026 system module")
    if target != "dsprhbm" and cache.get("CP2K_USE_CUSOLVER_MP") != "ON":
        raise ValueError("cuSOLVERMp was not enabled")
    return cache


def check_linkage(text, target):
    if "not found" in text or TRANSIENT.search(text):
        raise ValueError("unresolved or transient CP2K dynamic dependency")
    if "elpa-2024" in text:
        raise ValueError("CP2K linked the obsolete toolchain ELPA")
    if "libelpa" not in text or "/opt/devtools/elpa/elpa-2026.02.001-2603-gnu/" not in text:
        raise ValueError("ELPA 2026 dynamic linkage was not proven")
    if target != "dsprhbm":
        for library, root in (("libcusolverMp.so", "/opt/devtools/nvidia/mp_libs/lib/"),
                              ("libnccl.so", "/opt/devtools/nvidia/nccl_2.29.3_")):
            if not any(library in line and root in line for line in text.splitlines()):
                raise ValueError("wrong or missing runtime library: " + library)


def check_dynamic_paths(text):
    for line in text.splitlines():
        if "RPATH" in line or "RUNPATH" in line:
            if TRANSIENT.search(line):
                raise ValueError("installed ELF retains build-only RPATH")


def run(argv):
    return subprocess.run(argv, check=True, text=True, capture_output=True).stdout


def source_changes(prefix, sha):
    """Prove renamed/builtin features from this source, never silently waive them."""
    source = Path("/workspace/source")
    files = {name: (source / name).read_bytes() for name in
             ("src/CMakeLists.txt", "src/libgrpp_integrals.F", "CMakeLists.txt")}
    cmake = files["src/CMakeLists.txt"].decode()
    if "grpp/libgrpp.F" not in cmake or "grpp/grpp.c" not in cmake:
        raise ValueError("libgrpp is no longer proven built in")
    if "USE libgrpp" not in files["src/libgrpp_integrals.F"].decode():
        raise ValueError("libgrpp integration is absent")
    if "CP2K_USE_QUIP" in files["CMakeLists.txt"].decode():
        raise ValueError("QUIP support returned upstream; enable and test it")
    changes = {"source_sha": sha, "libgrpp": "builtin", "quip": "upstream_removed",
               "evidence": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    (Path(prefix) / "share/sai/upstream-feature-changes.json").write_text(json.dumps(changes, sort_keys=True) + "\n")


def verify(prefix, target, sha):
    prefix = Path(prefix)
    metadata = prefix / "share/sai"
    if (metadata / "source-sha").read_text().strip() != sha or (metadata / "target").read_text().strip() != target:
        raise ValueError("artifact provenance mismatch")
    check_cache((metadata / "CMakeCache.txt").read_text(), prefix, target)
    env = (metadata / "runtime-env.sh").read_text()
    if TRANSIENT.search(env):
        raise ValueError("saved environment depends on build-only paths")
    for name in ("BASIS_MOLOPT", "GTH_POTENTIALS"):
        if not (prefix / "share/cp2k/data" / name).is_file():
            raise ValueError("installed CP2K scientific data missing: " + name)
    if os.environ.get("CP2K_DATA_DIR") != str(prefix / "share/cp2k/data"):
        raise ValueError("CP2K_DATA_DIR must resolve to installed /opt data")
    version = run([str(prefix / "bin/cp2k.psmp"), "--version"])
    flags = check_flags(version, target)
    linkage = run(["ldd", str(prefix / "bin/cp2k.psmp")])
    check_linkage(linkage, target)
    for binary in [prefix / "bin/cp2k.psmp", *sorted((prefix / "lib64").glob("libcp2k.so.*"))]:
        check_dynamic_paths(run(["readelf", "-d", str(binary)]))
    print(version, end="")
    print(linkage, end="")
    print(json.dumps({"artifact_checks": True, "scientific_acceptance": False,
                      "prefix": str(prefix), "target": target, "flags": flags}, sort_keys=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("op", choices=("cache", "verify", "source-changes"))
    parser.add_argument("prefix", type=Path)
    parser.add_argument("target")
    parser.add_argument("sha")
    args = parser.parse_args()
    if args.op == "cache":
        check_cache(Path("/workspace/build/CMakeCache.txt").read_text(), args.prefix, args.target)
    elif args.op == "source-changes":
        source_changes(args.prefix, args.sha)
    else:
        verify(args.prefix, args.target, args.sha)


if __name__ == "__main__":
    main()
