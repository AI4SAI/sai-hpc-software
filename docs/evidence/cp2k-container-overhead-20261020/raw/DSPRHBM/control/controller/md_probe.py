#!/usr/bin/env python3
"""Read-only installed-stack inventory. C API enumeration avoids GPU initialization.

An inventory is deliberately NOT scientific acceptance. Run inference and the
LAMMPS/PLUMED cases separately, on allocated compute nodes.
"""
import argparse
import ctypes
from contextlib import contextmanager
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


@contextmanager
def suppress_native_output():
    """Silence C/C++ banners while probing through ctypes.

    LAMMPS can write directly to process fd 1/2 even with ``-screen none``.
    Without this guard those bytes corrupt the JSON stream written by the
    caller. Python's ``redirect_stdout`` is insufficient for native writes.
    """
    libc = ctypes.CDLL(None)
    libc.fflush(None)
    saved = (os.dup(1), os.dup(2))
    try:
        with open(os.devnull, "w") as sink:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
        yield
    finally:
        libc.fflush(None)
        os.dup2(saved[0], 1)
        os.dup2(saved[1], 2)
        os.close(saved[0])
        os.close(saved[1])


def lammps_inventory(prefix):
    # The trusted recipe installs shared objects under lib64 on SAI's x86_64
    # toolchain, while older site stacks use lib. Probe both ABI-standard
    # locations; do not fall back to another LAMMPS installation.
    root = Path(prefix)
    paths = sorted({path for directory in (root / "lib", root / "lib64")
                    for path in directory.glob("liblammps.so*")})
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
    lib.lammps_style_count.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.lammps_style_name.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    lib.lammps_version.argtypes = [ctypes.c_void_p]
    lib.lammps_close.argtypes = [ctypes.c_void_p]
    with suppress_native_output():
        handle = lib.lammps_open_no_mpi(len(args), args, None)
        if not handle:
            raise ValueError("cannot enumerate installed LAMMPS styles")
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
