#!/usr/bin/env python3
"""Bind one MPI rank to one allocated CUDA device and record its identity."""
import ctypes
import json
import os
from pathlib import Path
import socket
import sys
import uuid

from source_cache import checksum


def main():
    count, binary, *argv = sys.argv[1:]
    rank, local = int(os.environ['OMPI_COMM_WORLD_RANK']), int(os.environ['OMPI_COMM_WORLD_LOCAL_RANK'])
    count = int(count)
    if count < 1 or local >= count:
        raise ValueError('more MPI ranks than allocated test devices')
    driver = ctypes.CDLL('libcuda.so.1')

    def call(name, *arguments):
        code = getattr(driver, name)(*arguments)
        if code:
            raise RuntimeError(f'{name} failed with CUDA driver code {code}')

    call('cuInit', 0)
    available, device = ctypes.c_int(), ctypes.c_int()
    call('cuDeviceGetCount', ctypes.byref(available))
    if available.value < count:
        raise ValueError('not enough CUDA-visible devices')
    call('cuDeviceGet', ctypes.byref(device), local)
    identifier = (ctypes.c_ubyte * 16)()
    call('cuDeviceGetUuid', ctypes.byref(identifier), device)
    gpu = 'GPU-' + str(uuid.UUID(bytes=bytes(identifier)))
    major, minor = ctypes.c_int(), ctypes.c_int()
    call('cuDeviceComputeCapability', ctypes.byref(major), ctypes.byref(minor), device)
    if (major.value, minor.value) != (7, 0):
        raise ValueError('acceptance requires an SM70 V100')
    record = dict(rank=rank, local_rank=local, host=socket.gethostname().split('.')[0], gpu_uuid=gpu,
                  binary=binary, binary_sha256=checksum(Path(binary)), pid=os.getpid(),
                  cuda_visible_devices=os.environ.get('CUDA_VISIBLE_DEVICES'),
                  sm=70, argv=argv)
    os.environ['CUDA_VISIBLE_DEVICES'] = gpu
    folder = Path('ranks')
    folder.mkdir(exist_ok=True)
    stem = Path(argv[argv.index('-in') + 1]).name
    with (folder / f'{stem}-{rank}.json').open('x') as stream:
        json.dump(record, stream, indent=2)
    os.execv(binary, [binary, *argv])


if __name__ == '__main__':
    main()
