#!/usr/bin/env python3
"""Validate the candidate module on all four physical installations."""
import argparse
import importlib.util
import json
from pathlib import Path
import re

from install import NAME, checksum, require

PACKAGE = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, PACKAGE.with_name('elpa-2026.02.001')/filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


numeric = load('numeric', 'verify-native-results.py')
lifecycle = load('lifecycle', 'verify-lifecycle.py')


def validate(root):
    release = json.loads((PACKAGE/'release.json').read_text())
    control = root/'control'
    frozen = json.loads((control/'release.json').read_text())
    require(frozen['builds'] == release['builds'], 'Library identities changed')
    for line in (root/'controls.sha256').read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        require(Path(name).name == name and checksum(control/name) == digest, 'Frozen control changed')
    require(checksum(control/NAME) == release['module_sha256'], 'Wrong candidate module')
    require(checksum(control/'previous-auto') == release['previous_module_sha256'], 'Wrong previous module')
    jobs = {}
    for line in (root/'accounting.txt').read_text().splitlines():
        job, partition, state, code, elapsed, node = line.split('|')
        jobs[job] = dict(partition=partition, state=state, exit_code=code, elapsed=elapsed, node=node)
    report = dict(execution='physical /opt libraries; candidate module in home',
                  scope='single node, two MPI ranks; correctness and object lifetime, not performance',
                  module_sha256=release['module_sha256'], partitions={})
    for partition, (isa, gpu) in numeric.PARTITIONS.items():
        output = root/partition
        job = (output/'job.id').read_text().strip()
        info = jobs[job]
        require(info['partition'] == partition and info['state'] == 'COMPLETED' and info['exit_code'] == '0:0',
                f'Job not successfully completed: {job}')
        require((output/'hostname.txt').read_text().strip().split('.')[0] == info['node'], 'Wrong compute node')
        require('ELPA_REPAIRED_PHYSICAL_FILES_PASS' in (output/f'slurm-{job}.log').read_text(), 'Physical bytes not verified')
        backend = 'nvidia' if gpu else 'cpu'
        prefix = f'/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-{isa}/{backend}'
        require((output/'passed.txt').read_text().strip() == f'ELPA_PARTITION_MODULE_PASS {partition} {isa} {backend}',
                'Missing module acceptance')
        require((output/'selection.txt').read_text().splitlines() ==
                [f'partition={partition}', f'isa={isa}', f'backend={backend}', f'prefix={prefix}'], 'Wrong selection')
        require((output/'module.sha256').read_text().split()[0] == release['module_sha256'], 'Tested module changed')
        require(checksum(output/'modulefiles/elpa'/NAME) == release['module_sha256'], 'Final module changed')
        require('ELPA_FORTRAN_CONSUMER_PASS' in (output/'fortran-smoke.log').read_text(), 'Fortran check failed')
        require((output/'previous-unload.txt').read_text().strip() == 'ELPA_PREVIOUS_AUTO_UNLOAD_PASS', 'Upgrade unload failed')
        if gpu:
            require((output/'legacy-unload.txt').read_text().strip() == 'ELPA_LEGACY_AUTO_UNLOAD_PASS', 'Legacy unload failed')
        require('inside a Slurm job or allocation' in ' '.join((output/'missing-partition.log').read_text().split()),
                'Missing partition accepted')
        require('Unsupported ELPA partition' in (output/'unsupported-partition.log').read_text(), 'Unsupported partition accepted')
        expected = [prefix+'/lib/libelpa_openmp.so.19',
                    f'/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-{isa}/lib/libmpi.so.40',
                    f'/opt/devtools/saiblas/2603-gnu-{isa}/lib/libopenblas.so.0',
                    '/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/comm_libs/12.9/hpcx/hpcx-2.25.1/hcoll/lib/libhcoll.so.1']
        for filename in ('ldd.txt', 'consumer-ldd.txt'):
            text = (output/filename).read_text()
            require('not found' not in text and '/opt/mellanox/hcoll' not in text, 'Unresolved or legacy library')
            if not gpu:
                require(not re.search(r'lib(?:cuda|cublas|cusolver)', text), 'CPU library depends on CUDA')
        require(all(lib in (output/'consumer-ldd.txt').read_text() for lib in expected), 'Unexpected linked libraries')
        loaders = list(output.glob('loader.*'))
        require(len(loaders) == 2, 'Missing rank loader trace')
        for path in loaders:
            loaded = re.findall(r'calling init:\s+(\S+)', path.read_text())
            require(all(lib in loaded for lib in expected), 'Unexpected runtime libraries')
        errors = numeric.parse_case((output/'numeric.log').read_text(), 2, gpu, 2, 1, 1)
        traces = [lifecycle.validate((output/f'lifecycle-g{mode}.log').read_text(), 2, mode, 32)
                  for mode in range(gpu+1)]
        for trace in traces:
            for key in errors:
                errors[key] = max(errors[key], trace['max_errors'][key])
        report['partitions'][partition] = dict(job_id=job, **info, prefix=prefix, backend=backend,
            numerical_solves=2+sum(t['numerical_solves'] for t in traces), max_errors=errors, lifecycle=traces)
    report['numerical_solves'] = sum(p['numerical_solves'] for p in report['partitions'].values())
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    args = parser.parse_args()
    print(json.dumps(validate(args.results), indent=2, sort_keys=True))
