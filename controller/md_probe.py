#!/usr/bin/env python3
"""Read-only installed-stack inventory. C API enumeration avoids GPU initialization.

An inventory is deliberately NOT scientific acceptance. Run inference and the
LAMMPS/PLUMED cases separately, on allocated compute nodes.
"""
import argparse
import ctypes
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess


def run(*argv):
    return subprocess.check_output(argv, text=True, stderr=subprocess.STDOUT)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def libraries(path):
    output = run("ldd", str(path))
    if "not found" in output:
        raise ValueError(f"unresolved library dependency: {path}\n{output}")
    return output


def lammps_inventory(prefix):
    paths = sorted((Path(prefix) / "lib").glob("liblammps.so*"))
    if not paths:
        raise ValueError("shared liblammps required for complete feature enumeration")
    library = paths[0].resolve()
    lib = ctypes.CDLL(str(library), mode=ctypes.RTLD_GLOBAL)
    lib.lammps_config_package_count.restype = ctypes.c_int
    lib.lammps_config_package_name.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    packages = []
    for index in range(lib.lammps_config_package_count()):
        buf = ctypes.create_string_buffer(256)
        lib.lammps_config_package_name(index, buf, len(buf))
        packages.append(buf.value.decode())
    lib.lammps_open_no_mpi.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_void_p)]
    lib.lammps_open_no_mpi.restype = ctypes.c_void_p
    args = (ctypes.c_char_p * 5)(b"lmp", b"-screen", b"none", b"-log", b"none")
    handle = lib.lammps_open_no_mpi(len(args), args, None)
    if not handle:
        raise ValueError("cannot enumerate installed LAMMPS styles")
    lib.lammps_style_count.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.lammps_style_name.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    lib.lammps_version.argtypes = [ctypes.c_void_p]
    lib.lammps_close.argtypes = [ctypes.c_void_p]
    styles = {}
    try:
        for kind in ("pair", "bond", "angle", "dihedral", "improper", "kspace", "fix", "compute",
                     "region", "dump", "atom", "integrate", "minimize", "command"):
            values = []
            for index in range(lib.lammps_style_count(handle, kind.encode())):
                buf = ctypes.create_string_buffer(256)
                lib.lammps_style_name(handle, kind.encode(), index, buf, len(buf))
                values.append(buf.value.decode())
            styles[kind] = sorted(values)
        version = str(lib.lammps_version(handle))
    finally:
        lib.lammps_close(handle)
    return {"version": version, "packages": sorted(packages), "styles": styles,
            "library": str(library), "library_sha256": sha(library), "ldd": libraries(library)}


def inventory(lammps, deepmd, plumed):
    deepmd = Path(deepmd)
    python_site = next((deepmd / "lib").glob("python*/site-packages"))
    package = python_site / "deepmd"
    # Backend presence is an ABI inventory, not proof that inference executes.
    backend_libs = sorted(set([*(deepmd / "lib").glob("libdeepmd_backend*.so*"),
                               *(package / "lib").glob("libdeepmd_backend*.so*")]))
    backend_names = sorted({re.search(r"backend_([^.]+)", p.name).group(1) for p in backend_libs})
    distributions = {}
    for name in ("deepmd-kit", "torch", "tensorflow", "tensorflow_cpu", "jax", "jaxlib", "numpy"):
        try:
            distributions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    if not all(name in distributions for name in ("deepmd-kit", "torch", "jax", "jaxlib")):
        raise ValueError("required system backend distributions unavailable")
    if not any(name in distributions for name in ("tensorflow", "tensorflow_cpu")):
        raise ValueError("TensorFlow backend unavailable")
    kernel = Path(plumed) / "lib/libplumedKernel.so"
    config = run(str(Path(plumed) / "bin/plumed"), "info", "--configuration")
    features = sorted(set(re.findall(r"-D(__PLUMED_[A-Z0-9_]+)=1(?:\s|$)", config)))
    if '-fopenmp' in config:
        features.append('OPENMP')
    return {"schema": 1, "node": os.uname().nodename,
            "lammps": lammps_inventory(lammps),
            "deepmd": {"version": distributions["deepmd-kit"], "backends": backend_names,
                       "distributions": distributions,
                       "libraries": {str(p): {"sha256": sha(p), "ldd": libraries(p)} for p in backend_libs}},
            "plumed": {"version": run(str(Path(plumed) / "bin/plumed"), "info", "--long-version").strip(),
                       "kernel_sha256": sha(kernel), "kernel": str(kernel), "ldd": libraries(kernel),
                       "features": features,
                       "configuration": config}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--lammps", required=True)
    parser.add_argument("--deepmd", required=True)
    parser.add_argument("--plumed", default="/opt/apps/plumed/plumed-2.10.1")
    args = parser.parse_args()
    print(json.dumps(inventory(args.lammps, args.deepmd, args.plumed), sort_keys=True))
