#!/usr/bin/env python3
"""Independently validate raw physical ELPA results and completed Slurm jobs."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re

PARTITIONS = {'16V100': ('avx2', 1), '4V100': ('avx512', 1),
              '8V100V0': ('avx2', 1), 'DSPRHBM': ('avx512', 0)}
ERRORS = ('spectrum', 'residual', 'orthogonality')
MODULE = 'elpa/2026.02.001-2603-gnu-auto'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_case(text, ranks, gpu, solver, complex_value, nbc):
    # The site's MPI shared-filesystem advisory ends with a NUL before the next
    # stdout line. Preserve the raw log; trim that prefix only, never numeric data.
    lines = [line.lstrip('\0') for line in text.splitlines()]
    lines = [line for line in lines if line.startswith('ELPA_NUMERIC ')]
    require(len(lines) == 2, 'Expected exactly two numerical results')
    maxima = dict.fromkeys(ERRORS, 0.0)
    for repeat, line in enumerate(lines):
        tokens = line.split()
        require(tokens[:2] == ['ELPA_NUMERIC', 'PASS'], 'Numerical solve failed')
        row = dict(token.split('=', 1) for token in tokens[2:])
        expected = dict(ranks=ranks, gpu=gpu, solver=solver, complex=complex_value,
                        nonblocking=nbc, repeat=repeat)
        require(all(row.get(k) == str(v) for k, v in expected.items()),
                'Numerical case identity mismatch')
        for key in ERRORS:
            value = float(row[key])
            require(math.isfinite(value) and 0 <= value < 1e-10,
                    f'Invalid numerical error: {key}={value}')
            maxima[key] = max(maxima[key], value)
    return maxima


def validate(root, partitions=None, api='explicit', release_path=None):
    partitions = list(PARTITIONS) if partitions is None else partitions
    require(partitions and len(set(partitions)) == len(partitions) and
            set(partitions) <= set(PARTITIONS), 'Invalid partition selection')
    require(api in ('explicit', 'host'), 'Invalid C API selection')
    control = root/'control'
    for line in (root/'controls.sha256').read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        require(Path(name).name == name, 'Invalid control checksum path')
        require(sha256(control/name) == digest, f'Changed frozen control: {name}')
    package = Path(__file__).resolve().parent
    release = json.loads((control/'release.json').read_text())
    require(release == json.loads((release_path or package/'release.json').read_text()), 'Release identity changed')
    require(sha256(control/MODULE.split('/')[1]) == release['module_sha256'], 'Wrong module')
    cpu_supplement = 'gpu_prerequisites' in release
    jobs = {}
    for line in (root/'accounting.txt').read_text().splitlines():
        job, partition, state, code, elapsed, node = line.split('|')
        jobs[job] = dict(partition=partition, state=state, exit_code=code, elapsed=elapsed, node=node)
    result = dict(status='accepted_for_selected_partitions_and_api', c_api=api,
                  execution='physical /opt, without Apptainer',
                  scope='single node per partition, one/two MPI ranks; small correctness tests',
                  unaccepted_partitions=sorted(set(PARTITIONS) - set(partitions)),
                  partitions={})
    for partition in partitions:
        isa, gpu_max = PARTITIONS[partition]
        output = root/partition
        job_id = (output/'job.id').read_text().strip()
        job = jobs[job_id]
        require(job['partition'] == partition and job['state'] == 'COMPLETED' and
                job['exit_code'] == '0:0', f'Job not successfully completed: {job_id}')
        node = (output/'hostname.txt').read_text().strip().split('.')[0]
        require(node == job['node'], f'Unexpected compute node: {partition}')
        require((output/'identity.txt').read_text().splitlines() ==
                [job_id, partition, isa, str(gpu_max)], 'Execution identity mismatch')
        api_file = output/'c-api.txt'
        actual_api = api_file.read_text().strip() if api_file.exists() else 'explicit'
        require(actual_api == api, 'C API identity mismatch')
        marker = f'ELPA_PHYSICAL_VERIFIED {partition} {isa}'
        if api_file.exists():
            marker += f' api={api}'
        require((output/'passed.txt').read_text().strip() == marker, 'Completion marker missing')
        require('ELPA_FORTRAN_CONSUMER_PASS' in (output/'fortran-smoke.log').read_text(),
                'Fortran consumer failed')
        require((output/'pkg-version.txt').read_text().strip() == release['version'], 'Wrong version')
        require(MODULE in (output/'modules.txt').read_text().splitlines(), 'Public module not loaded')
        backend = 'cpu' if cpu_supplement and partition == 'DSPRHBM' else 'nvidia'
        installed = (output/'installed-files.txt').read_text()
        if cpu_supplement:
            require(all(text in installed for text in
                        ['avx2: existing GPU prerequisite verified',
                         'avx512: existing GPU prerequisite verified',
                         'Physical CPU ELPA and partition-aware auto module verified;']),
                    'Installed CPU/GPU inventories or auto module unverified')
            require((output/'backend.txt').read_text().strip() == backend, 'Wrong ELPA backend')
            require((output/'modulefile.txt').read_text().strip() ==
                    '/opt/modules/modulefiles/devtools/'+MODULE, 'Physical public module not used')
            require((output/'module.sha256').read_text().split()[0] == release['module_sha256'],
                    'Installed auto module changed')
            prefix = release['prefix'] if backend == 'cpu' else release['gpu_prerequisites'][isa]['prefix']
        else:
            require('avx2: physical installation verified' in installed
                    and 'avx512: physical installation verified' in installed,
                    'Installed inventories unverified')
            prefix = release['builds'][isa]['prefix']
        require(prefix == f'/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-{isa}/{backend}',
                'Unexpected ELPA prefix')
        expected_libs = [prefix+'/lib/libelpa_openmp.so.19',
                         f'/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-{isa}/lib/libmpi.so.40',
                         f'/opt/devtools/saiblas/2603-gnu-{isa}/lib/libopenblas.so.0',
                         '/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/comm_libs/12.9/hpcx/hpcx-2.25.1/hcoll/lib/libhcoll.so.1']
        for name in ('ldd.txt', 'consumer-ldd.txt', 'dynamic.txt'):
            text = (output/name).read_text()
            require('not found' not in text and '/opt/mellanox/hcoll' not in text,
                    f'Unresolved or legacy library: {name}')
        if backend == 'cpu':
            require(not re.search(r'lib(?:cuda|cublas|cusolver)',
                                 (output/'ldd.txt').read_text() + (output/'dynamic.txt').read_text()),
                    'CPU ELPA still depends on CUDA')
            require(not re.search(r'is_device_ptr|cudaPointerGetAttributes', (output/'symbols.txt').read_text()),
                    'CPU ELPA retained CUDA pointer dispatch')
        require(all(path in (output/'consumer-ldd.txt').read_text() for path in expected_libs),
                'Consumer linked unexpected libraries')
        loaders = sorted(output.glob('loader.*'))
        require(len(loaders) == 2, 'Expected loader evidence from two MPI processes')
        for path in loaders:
            loaded = re.findall(r'calling init:\s+(\S+)', path.read_text())
            require(all(lib in loaded for lib in expected_libs), 'Actual loader path mismatch')
            require(not any('/opt/mellanox/hcoll' in lib for lib in loaded), 'Legacy HCOLL loaded')
        maxima = dict.fromkeys(ERRORS, 0.0)
        expected_logs = set()
        for ranks in (1, 2):
            for gpu in range(gpu_max + 1):
                for solver in (1, 2):
                    for complex_value in (0, 1):
                        for nbc in ((0, 1) if solver == 2 else (0,)):
                            name = f'r{ranks}-g{gpu}-s{solver}-c{complex_value}-nbc{nbc}.log'
                            expected_logs.add(name)
                            errors = parse_case((output/name).read_text(), ranks, gpu, solver, complex_value, nbc)
                            for key in ERRORS:
                                maxima[key] = max(maxima[key], errors[key])
        require({p.name for p in output.glob('r*-g*-s*-c*-nbc*.log')} == expected_logs,
                'Unexpected numerical case inventory')
        result['partitions'][partition] = dict(job_id=job_id, **job, isa=isa, backend=backend, prefix=prefix,
            numerical_solves=2*len(expected_logs), max_errors=maxima, loaded_libraries=expected_libs)
    result['total_solves'] = sum(p['numerical_solves'] for p in result['partitions'].values())
    evidence = [p for p in root.rglob('*') if p.is_file() and
                p.name not in {'smoke', 'fortran-smoke', 'physical-native-acceptance.json'}]
    result['evidence_sha256'] = {str(p.relative_to(root)): sha256(p) for p in sorted(evidence)}
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    parser.add_argument('--partitions', nargs='+', choices=list(PARTITIONS), default=list(PARTITIONS))
    parser.add_argument('--api', choices=['explicit', 'host'], default='explicit')
    parser.add_argument('--release', type=Path, help='Pin the installed CPU supplement release metadata')
    args = parser.parse_args()
    print(json.dumps(validate(args.results.resolve(), args.partitions, args.api, args.release), indent=2, sort_keys=True))
