#!/usr/bin/env python3
"""Record the actual final-container CPU binding, then exec the measured child."""
import json
import os
from pathlib import Path
import sys

from md_science import require_execution_context


def binding_record(expected_cpu, topology=Path('/sys/devices/system/cpu')):
    if type(expected_cpu) is not int or expected_cpu < 0:
        raise ValueError('one explicit allocated CPU is required')
    observed = sorted(os.sched_getaffinity(0))
    if observed != [expected_cpu]:
        raise ValueError('actual final-container affinity differs from the fixed benchmark binding')
    location = topology / f'cpu{expected_cpu}' / 'topology'
    return {'logical_cpus': observed,
            'physical_cores': [[int((location / 'physical_package_id').read_text()),
                                int((location / 'core_id').read_text())]],
            'omp_threads': os.environ.get('OMP_NUM_THREADS'),
            'dp_intra_threads': os.environ.get('DP_INTRA_OP_PARALLELISM_THREADS'),
            'dp_inter_threads': os.environ.get('DP_INTER_OP_PARALLELISM_THREADS')}


def main(argv=None):
    require_execution_context()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or not Path(argv[0]).is_absolute() or Path.cwd() != Path('/work'):
        raise ValueError('benchmark child must be absolute and run in the mounted task directory')
    raw_cpu = os.environ.get('SAI_MD_PERFORMANCE_CPU', '')
    if not raw_cpu.isascii() or not raw_cpu.isdecimal():
        raise ValueError('missing benchmark binding contract')
    record = binding_record(int(raw_cpu))
    if any(record[key] != '1' for key in ('omp_threads', 'dp_intra_threads', 'dp_inter_threads')):
        raise ValueError('benchmark requires one actual OpenMP and DeepMD worker thread')
    record.update(node=os.uname().nodename, job=os.environ['SAI_MD_ALLOCATED_JOB'], argv=argv)
    with Path('performance-binding.json').open('x') as stream:
        json.dump(record, stream, sort_keys=True)
        stream.write('\n')
    os.execv(argv[0], argv)


if __name__ == '__main__':
    main()
