#!/usr/bin/env python3
"""Compile DBCSR transpose kernels without a CUDA context or CP2K calculation."""

import argparse
import ctypes as C
import faulthandler
import hashlib
import json
import os
from pathlib import Path
import socket
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dbcsr-source", type=Path, required=True)
    parser.add_argument("--library", default="libnvrtc.so.12")
    parser.add_argument("--repeat", type=int, default=2)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    faulthandler.enable()

    def emit(**record):
        print(json.dumps(record, sort_keys=True), flush=True)

    lib = C.CDLL(args.library)
    signatures = {
        "Version": [C.POINTER(C.c_int), C.POINTER(C.c_int)],
        "CreateProgram": [C.POINTER(C.c_void_p), C.c_char_p, C.c_char_p,
                          C.c_int, C.POINTER(C.c_char_p), C.POINTER(C.c_char_p)],
        "AddNameExpression": [C.c_void_p, C.c_char_p],
        "CompileProgram": [C.c_void_p, C.c_int, C.POINTER(C.c_char_p)],
        "GetProgramLogSize": [C.c_void_p, C.POINTER(C.c_size_t)],
        "GetProgramLog": [C.c_void_p, C.c_void_p],
        "GetPTXSize": [C.c_void_p, C.POINTER(C.c_size_t)],
        "GetPTX": [C.c_void_p, C.c_void_p],
        "DestroyProgram": [C.POINTER(C.c_void_p)],
    }
    for name, argtypes in signatures.items():
        func = getattr(lib, "nvrtc" + name)
        func.argtypes = argtypes
        func.restype = C.c_int

    def call(name, *params):
        status = getattr(lib, "nvrtc" + name)(*params)
        if status:
            raise RuntimeError(f"nvrtc{name}: status {status}")

    def maps():
        names = sorted({line.split()[-1] for line in Path("/proc/self/maps").read_text().splitlines()
                        if "/" in line and ("nvrtc" in line or "libcuda" in line)})
        emit(libraries=[{"path": name, "sha256": hashlib.sha256(Path(name).read_bytes()).hexdigest()}
                        for name in names])

    major, minor = C.c_int(), C.c_int()
    call("Version", C.byref(major), C.byref(minor))
    kernels = args.dbcsr_source / "src/acc/libsmm_acc/kernels"
    common = (kernels / "smm_acc_common.h").read_text()
    transpose = (kernels / "smm_acc_transpose.h").read_text()
    source = (common + transpose.replace('#include "smm_acc_common.h"', "")).encode()
    emit(host=socket.gethostname(), pid=os.getpid(), nvrtc=[major.value, minor.value],
         source_sha256=hashlib.sha256(source).hexdigest(), library=args.library,
         job=os.environ.get("SLURM_JOB_ID"))
    maps()
    options = (C.c_char_p * 3)(b"-D__CUDA", b"-w", b"--gpu-architecture=compute_70")
    sizes = [(5, 5), (5, 13), (13, 5), (13, 13), (13, 18), (18, 13),
             (18, 18), (32, 32), (64, 32), (64, 64)]
    for repeat in range(args.repeat):
        for m, n in sizes:
            program = C.c_void_p()
            call("CreateProgram", C.byref(program), source, b"transpose_kernel.cu", 0, None, None)
            try:
                call("AddNameExpression", program, f"transpose_d<{m}, {n}>".encode())
                emit(stage="compile", repeat=repeat, m=m, n=n)
                start = time.monotonic()
                status = lib.nvrtcCompileProgram(program, 3, options)
                log_size = C.c_size_t()
                call("GetProgramLogSize", program, C.byref(log_size))
                log = C.create_string_buffer(max(log_size.value, 1))
                call("GetProgramLog", program, log)
                emit(stage="compiled", status=status, seconds=time.monotonic() - start,
                     log=log.value.decode(errors="replace"), repeat=repeat, m=m, n=n)
                if status:
                    raise RuntimeError(f"nvrtcCompileProgram: status {status}")
                size = C.c_size_t()
                call("GetPTXSize", program, C.byref(size))
                ptx = C.create_string_buffer(size.value)
                call("GetPTX", program, ptx)
                if repeat == 0 and (m, n) == sizes[0]:
                    maps()
            finally:
                call("DestroyProgram", C.byref(program))
    emit(stage="passed", count=args.repeat * len(sizes))


if __name__ == "__main__":
    main()
