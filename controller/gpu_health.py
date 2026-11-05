#!/usr/bin/env python3
"""Probe allocated CUDA devices on the host before a beta calculation."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import socket

CUDA_RUNTIME = '/opt/devtools/nvidia/cuda-12.9.1/lib64/libcudart.so.12'


def ecc_fault(report):
    statuses = [report.get('device_count_status', {})]
    statuses += [device.get(key, {}) for device in report.get('devices', [])
                 for key in ('set_device', 'context_init')]
    return any(status.get('code') == 214 for status in statuses)


def probe():
    report = dict(schema=1, node=os.environ.get('SLURMD_NODENAME', socket.gethostname().split('.')[0]),
                  hostname=socket.gethostname(), job=os.environ.get('SLURM_JOB_ID'),
                  partition=os.environ.get('SLURM_JOB_PARTITION'), workdir=str(Path.cwd()),
                  cuda_visible_devices=os.environ.get('CUDA_VISIBLE_DEVICES'),
                  probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  cuda_runtime=CUDA_RUNTIME, devices=[])
    try:
        runtime = ctypes.CDLL(CUDA_RUNTIME)
        runtime.cudaGetErrorString.argtypes = [ctypes.c_int]
        runtime.cudaGetErrorString.restype = ctypes.c_char_p
        runtime.cudaGetDeviceCount.argtypes = [ctypes.POINTER(ctypes.c_int)]
        runtime.cudaSetDevice.argtypes = [ctypes.c_int]
        runtime.cudaFree.argtypes = [ctypes.c_void_p]

        def status(code):
            return dict(code=code, message=runtime.cudaGetErrorString(code).decode())

        count = ctypes.c_int()
        report['device_count_status'] = status(runtime.cudaGetDeviceCount(ctypes.byref(count)))
        report['device_count'] = count.value
        if report['device_count_status']['code'] == 0:
            for ordinal in range(count.value):
                device = dict(ordinal=ordinal, set_device=status(runtime.cudaSetDevice(ordinal)))
                if device['set_device']['code'] == 0:
                    device['context_init'] = status(runtime.cudaFree(None))
                report['devices'].append(device)
        report['healthy'] = bool(count.value > 0 and report['device_count_status']['code'] == 0
            and all(device['set_device']['code'] == 0 and device['context_init']['code'] == 0
                    for device in report['devices']))
    except Exception as error:
        report.update(healthy=False, error=str(error))
    return report


if __name__ == '__main__':
    result = probe()
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    # Only a CUDA-reported uncorrectable ECC error authorizes hardware retry.
    raise SystemExit(0 if result['healthy'] else 74 if ecc_fault(result) else 1)
