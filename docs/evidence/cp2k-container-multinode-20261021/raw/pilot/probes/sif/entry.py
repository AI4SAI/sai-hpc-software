#!/usr/bin/env python3
"""Select a node-local allocated GPU, then launch identical native/SIF ranks."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import uuid


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def checksum(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def gpu_inventory():
    driver = ctypes.CDLL('libcuda.so.1')
    def checked(name, *args):
        result = getattr(driver, name)(*args)
        if result != 0:
            raise RuntimeError(f'{name}: CUDA error {result}')
    checked('cuInit', 0)
    count = ctypes.c_int()
    checked('cuDeviceGetCount', ctypes.byref(count))
    result = []
    for ordinal in range(count.value):
        device = ctypes.c_int()
        checked('cuDeviceGet', ctypes.byref(device), ordinal)
        ident = ctypes.create_string_buffer(16)
        name = ctypes.create_string_buffer(256)
        pci = ctypes.create_string_buffer(32)
        checked('cuDeviceGetUuid', ctypes.byref(ident), device)
        checked('cuDeviceGetName', name, len(name), device)
        checked('cuDeviceGetPCIBusId', pci, len(pci), device)
        result.append(dict(uuid='GPU-' + str(uuid.UUID(bytes=ident.raw)),
                           name=name.value.decode(), pci_bus=pci.value.decode()))
    return result


def context():
    affinity = sorted(os.sched_getaffinity(0))
    topology = {}
    for cpu in affinity:
        base = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
        topology[str(cpu)] = [int((base/'physical_package_id').read_text()),
                              int((base/'core_id').read_text())]
    info = Path.cwd().stat()
    return dict(uid=os.getuid(), euid=os.geteuid(), host=socket.gethostname().split('.')[0],
                affinity=affinity, topology=topology, cwd=str(Path.cwd()),
                cwd_dev=info.st_dev, cwd_inode=info.st_ino,
                gpu=os.environ.get('CUDA_VISIBLE_DEVICES'),
                environment={key: os.environ.get(key) for key in [
                    'OMP_NUM_THREADS', 'OMP_PLACES', 'OMP_PROC_BIND', 'OMP_STACKSIZE',
                    'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'CP2K_DATA_DIR', 'MPI_HOME',
                    'TMPDIR', 'CUDA_CACHE_PATH', 'XDG_CACHE_HOME']})


def inventory(directory):
    record = context()
    # Numeric Slurm visibility can use physical indices while CUDA renumbers
    # devices after cgroup filtering. Enumerate the device cgroup directly;
    # refuse to proceed unless it exposes exactly the four allocated devices.
    record['slurm_cuda_visible_devices'] = os.environ.pop('CUDA_VISIBLE_DEVICES', None)
    record['visibility_basis'] = 'CUDA driver enumeration within the Slurm device cgroup, without numeric CUDA_VISIBLE_DEVICES'
    record['devices'] = gpu_inventory()
    record['slurm_job'] = os.environ['SLURM_JOB_ID']
    record['slurm_gpu_environment'] = {key: value for key, value in os.environ.items()
                                     if key.startswith('SLURM_') and 'GPU' in key}
    write(Path(directory)/(record['host'] + '.json'), record)
    assert len(record['devices']) == 4
    assert all('V100' in device['name'] for device in record['devices'])


def inside(operation, args):
    rank = int(os.environ['OMPI_COMM_WORLD_RANK'])
    local = int(os.environ['OMPI_COMM_WORLD_LOCAL_RANK'])
    prefix = Path(os.environ['SAI_MULTI_PREFIX'])
    binary = prefix/'bin/cp2k.psmp'
    record = context()
    record.update(rank=rank, local_rank=local, size=int(os.environ['OMPI_COMM_WORLD_SIZE']),
                  pid=os.getpid(), binary=str(binary), binary_dev=binary.stat().st_dev)
    write(f'ranks/inside-{rank}.json', record)
    if operation == 'probe':
        record['devices'] = gpu_inventory()
        record['binary_sha256'] = checksum(binary)
        if local == 0:
            ldd = subprocess.check_output(['ldd', str(binary)], text=True)
            assert 'not found' not in ldd
            Path(f'ranks/ldd-{rank}.txt').write_text(ldd)
            record['libraries'] = {name: dict(path=path, sha256=checksum(path))
                for name, path in re.findall(r'^\s*(\S+)\s+=>\s+(/\S+)', ldd, re.M)}
            record['data'] = {name: checksum(prefix/'share/cp2k/data'/name)
                              for name in ['BASIS_MOLOPT', 'GTH_POTENTIALS']}
            Path(f'ranks/mountinfo-{rank}.txt').write_text(Path('/proc/self/mountinfo').read_text())
        write(f'ranks/probe-{rank}.json', record)
    else:
        assert operation == 'cp2k'
        os.execv(str(binary), [str(binary), *args])


def host(mode, operation, args):
    rank = int(os.environ['OMPI_COMM_WORLD_RANK'])
    local = int(os.environ['OMPI_COMM_WORLD_LOCAL_RANK'])
    assert int(os.environ['OMPI_COMM_WORLD_SIZE']) == 8 and 0 <= local < 4
    node = socket.gethostname().split('.')[0]
    allocation = json.loads((Path(os.environ['SAI_MULTI_INVENTORY'])/(node+'.json')).read_text())
    assert allocation['slurm_job'] == os.environ['SLURM_JOB_ID']
    os.environ['CUDA_VISIBLE_DEVICES'] = allocation['devices'][local]['uuid']
    runtime = Path(os.environ['SAI_MULTI_RUNTIME'])
    rank_runtime = runtime/node/str(local)
    for key, relative in [('TMPDIR', ''), ('CUDA_CACHE_PATH', 'cuda'),
                          ('XDG_CACHE_HOME', 'cache'), ('APPTAINER_TMPDIR', 'apptainer'),
                          ('APPTAINER_CACHEDIR', 'apptainer-cache')]:
        path = rank_runtime/relative
        path.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(path)
    record = context()
    record.update(rank=rank, local_rank=local, size=8)
    write(f'ranks/host-{rank}.json', record)
    work = str(Path.cwd())
    command = ['/usr/bin/python3', work+'/entry.py', 'inside', operation, *args]
    if mode == 'sif':
        launch = ['apptainer', 'exec', '--cleanenv', '--no-home', '--no-mount',
                  'bind-paths,home,cwd,tmp,hostfs', '--nv', '--pwd', work]
        for path in ['/usr', '/lib', '/lib64', '/opt/devtools']:
            launch += ['--bind', f'{path}:{path}:ro']
        launch += ['--bind', f'{work}:{work}:rw', '--bind', f'{runtime}:{runtime}:rw']
        topology = os.environ.get('NCCL_TOPO_FILE')
        if topology and not topology.startswith(str(runtime)+'/'):
            launch += ['--bind', f'{topology}:{topology}:ro']
        exact = {'CP2K_DATA_DIR', 'TMPDIR', 'XDG_CACHE_HOME', 'MPI_HOME',
                 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'SAI_MULTI_PREFIX'}
        for name, value in os.environ.items():
            if name in exact or name.startswith(('SLURM_', 'OMPI_', 'OPAL_', 'PMIX_', 'PMI_',
                    'PRTE_', 'UCX_', 'NCCL_', 'CUDA_', 'NVIDIA_', 'FI_', 'OMP_')):
                launch += ['--env', name+'='+value]
        launch += ['--env', 'LD_LIBRARY_PATH='+os.environ['LD_LIBRARY_PATH']+':/.singularity.d/libs',
                   os.environ['SAI_MULTI_IMAGE']]
        command = launch + command
    else:
        assert mode == 'native'
    command = ['/usr/bin/time', '-o', f'ranks/time-{rank}.txt', '-f', '%e %U %S %M', *command]
    os.execvp(command[0], command)


if __name__ == '__main__':
    if sys.argv[1] == 'inventory':
        inventory(sys.argv[2])
    elif sys.argv[1] == 'inside':
        inside(sys.argv[2], sys.argv[3:])
    else:
        assert sys.argv[1] == 'host'
        host(sys.argv[2], sys.argv[3], sys.argv[4:])
