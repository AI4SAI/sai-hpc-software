#!/usr/bin/env python3
"""Record physical-core binding and expose one distinct GPU per MPI rank."""
import json
import os
from pathlib import Path
import socket
import sys


def main():
    rank = int(os.environ['OMPI_COMM_WORLD_RANK'])
    local_rank = int(os.environ['OMPI_COMM_WORLD_LOCAL_RANK'])
    mode = os.environ['WATER_MODE']
    devices = json.loads(os.environ['WATER_GPU_UUIDS'])
    if mode == 'gpu':
        assert int(os.environ['OMPI_COMM_WORLD_SIZE']) == len(devices)
        os.environ['CUDA_VISIBLE_DEVICES'] = devices[local_rank]
    else:
        os.environ['CUDA_VISIBLE_DEVICES'] = ''
    affinity = sorted(os.sched_getaffinity(0))
    cores = {}
    for cpu in affinity:
        base = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
        cores[cpu] = [int((base/'physical_package_id').read_text()),
                      int((base/'core_id').read_text())]
    assert len({tuple(v) for v in cores.values()}) == int(os.environ['WATER_CORES_PER_RANK'])
    record = dict(rank=rank, local_rank=local_rank, pid=os.getpid(), host=socket.gethostname(),
                  affinity=affinity, cores=cores, gpu=os.environ['CUDA_VISIBLE_DEVICES'],
                  omp_threads=int(os.environ['OMP_NUM_THREADS']), binary=os.environ['WATER_BINARY'],
                  start_ticks=int(Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19]))
    Path(f'rank-{rank}.json').write_text(json.dumps(record, sort_keys=True)+'\n')
    os.execv(os.environ['WATER_BINARY'], [os.environ['WATER_BINARY'], *sys.argv[1:]])


if __name__ == '__main__':
    main()
