#!/usr/bin/env python3
"""Freeze, submit and independently verify the installed ELPA back-transform update."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import shutil
import subprocess

PACKAGE = Path(__file__).resolve().parent
PARTITIONS = {'16V100': ('avx2', 'nvidia'), '4V100': ('avx512', 'nvidia'),
              '8V100V0': ('avx2', 'nvidia'), 'DSPRHBM': ('avx512', 'cpu')}
NAME = '2026.02.001-2603-gnu-auto'


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def inventory(control):
    base = load('elpa_base', control / 'base_install.py')
    release = json.loads((control / 'release.json').read_text())
    previous = json.loads((control / 'cpu-release.json').read_text())
    require(checksum(control / 'base_install.py') == release['base_installer_sha256'], 'Installer changed')
    require(checksum(base.MODULE) == release['module_sha256'], 'Public module changed')
    result = dict(module_sha256=release['module_sha256'], variants={})
    for key in ('avx2', 'avx512', 'dsprhbm'):
        entry = (previous if key == 'dsprhbm' else release)['builds'][key]
        path = control / (key + '-manifest.json')
        require(checksum(path) == entry['manifest_sha256'], 'Manifest changed')
        prefix = base.PREFIXES[key]
        require(str(prefix) == entry['prefix'], 'Unexpected installed prefix')
        base.verify(prefix, json.loads(path.read_text()))
        result['variants'][key] = dict(prefix=str(prefix), manifest_sha256=entry['manifest_sha256'],
            library_sha256=checksum(prefix / 'lib/libelpa_openmp.so.19'))
    return result


def submit():
    repo = PACKAGE.parents[1]
    old = PACKAGE.with_name('elpa-2026.02.001-commfix')
    build = PACKAGE.with_name('elpa-2026.02.001')
    root = Path('/home/stardust/sai-hpc-software/runtime-tests') / (
        'elpa-backtransform-native-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    control = root / 'control'
    control.mkdir(parents=True, mode=0o755)
    files = {name: PACKAGE / name for name in ('native-checks.py', 'check-native.sbatch',
        'release.json', 'avx2-manifest.json', 'avx512-manifest.json')}
    files.update({name: old / name for name in ('check-partition.sh', NAME, 'dsprhbm-manifest.json')})
    files.update({'base_install.py': old / 'install.py', 'cpu-release.json': old / 'release.json',
                  'previous-auto': PACKAGE.with_name('elpa-2026.02.001-cpu') / NAME,
                  'legacy-auto': build / NAME, 'cp2k_elpa_abi.f90': repo / 'controller/cp2k_elpa_abi.f90'})
    files.update({name: build / name for name in ('smoke.c', 'comm-trace.c', 'verify-lifecycle.py', 'verify-native-results.py')})
    for name, source in files.items():
        shutil.copyfile(source, control / name)
    write_json(root / 'control-sha256.json', {p.name: checksum(p) for p in sorted(control.iterdir())})
    write_json(root / 'physical-inventory.json', inventory(control))
    jobs = {}
    write_json(root / 'jobs.json', jobs)
    for partition in PARTITIONS:
        output = root / partition
        output.mkdir()
        resources = ['--qos=rush-cpu', '--cpus-per-task=2'] if partition == 'DSPRHBM' else ['--qos=flood-1o2gpu', '--gpus-per-node=1']
        command = ['sbatch', '--hold', '--parsable', '--partition=' + partition,
            '--job-name=elpa-native-' + partition, '--output=' + str(output / 'slurm-%j.log'),
            *resources, str(control / 'check-native.sbatch'), str(control), str(output)]
        job = subprocess.check_output(command, text=True).strip().split(';')[0]
        require(job.isdigit(), 'Ambiguous held submission')
        (output / 'job.id').write_text(job + '\n')
        jobs[partition] = job
        write_json(root / 'jobs.json', jobs)
        subprocess.run(['scontrol', 'release', job], check=True)
        print(partition, job, output, flush=True)
    print(root, flush=True)


def verify(root, offline=False):
    control = root / 'control'
    hashes = json.loads((root / 'control-sha256.json').read_text())
    require(set(hashes) == {p.name for p in control.iterdir() if p.is_file()}, 'Control inventory changed')
    require(all(checksum(control / name) == digest for name, digest in hashes.items()), 'Frozen control changed')
    expected = json.loads((root / 'physical-inventory.json').read_text())
    if not offline:
        require(inventory(control) == expected, 'Physical installation changed since submission')
    jobs = json.loads((root / 'jobs.json').read_text())
    require(set(jobs) == set(PARTITIONS), 'Incomplete partition submission')
    if not offline:
        accounting = subprocess.check_output(['sacct', '-X', '-nP', '-j', ','.join(jobs.values()),
            '-o', 'JobIDRaw,Partition,State,ExitCode,Elapsed,NodeList'], text=True)
        (root / 'accounting.txt').write_text(accounting)
    rows = {row.split('|')[0]: row.split('|')[1:] for row in (root / 'accounting.txt').read_text().splitlines()}
    numeric = load('elpa_numeric', control / 'verify-native-results.py')
    lifecycle = load('elpa_lifecycle', control / 'verify-lifecycle.py')
    result = dict(execution='physical /opt and public module; no Apptainer',
        observed_utc=datetime.now(timezone.utc).isoformat(), partitions={})
    for partition, (isa, backend) in PARTITIONS.items():
        job = jobs[partition]
        require(rows[job][:3] == [partition, 'COMPLETED', '0:0'], 'Job not completed: ' + job)
        output = root / partition
        key = 'dsprhbm' if backend == 'cpu' else isa
        prefix = expected['variants'][key]['prefix']
        require((output / 'job.id').read_text().strip() == job, 'Job identity changed')
        require((output / 'hostname.txt').read_text().strip().split('.')[0] == rows[job][4], 'Unexpected node')
        for name in ('inventory-before.json', 'inventory-after.json'):
            require(json.loads((output / name).read_text()) == expected, 'Compute-node inventory differs')
        require((output / 'accepted.txt').read_text().strip() == 'ELPA_BACKTRANSFORM_NATIVE_PASS ' + partition, 'Missing native completion')
        require((output / 'passed.txt').read_text().strip() == f'ELPA_PARTITION_MODULE_PASS {partition} {isa} {backend}', 'Module checks failed')
        require((output / 'module-mode.txt').read_text().strip() == 'public', 'Not a public module test')
        require((output / 'modulefile.txt').read_text().strip() == '/opt/modules/modulefiles/devtools/elpa/' + NAME, 'Wrong module path')
        require((output / 'module.sha256').read_text().split()[0] == expected['module_sha256'], 'Wrong module bytes')
        gpu = int(backend == 'nvidia')
        errors = numeric.parse_case((output / 'numeric.log').read_text(), 2, gpu, 2, 1, 1)
        traces = [lifecycle.validate((output / f'lifecycle-g{mode}.log').read_text(), 2, mode, 32) for mode in range(gpu + 1)]
        require('ELPA_FORTRAN_CONSUMER_PASS' in (output / 'fortran-smoke.log').read_text(), 'Fortran check failed')
        require((output / 'gcc14-version.txt').read_text().strip() == '14.3.0', 'Wrong CP2K compiler')
        values = re.findall(r'CP2K_SITE_ELPA_GCC14_PASS\s+(\S+)\s+(\S+)', (output / 'gcc14.log').read_text())
        require(len(values) == 1 and all(math.isfinite(float(v)) and 0 <= float(v) < 1e-11 for v in values[0]), 'GCC14 numerical check failed')
        libraries = [prefix + '/lib/libelpa_openmp.so.19',
            f'/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-{isa}/lib/libmpi.so.40',
            f'/opt/devtools/saiblas/2603-gnu-{isa}/lib/libopenblas.so.0',
            '/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/comm_libs/12.9/hpcx/hpcx-2.25.1/hcoll/lib/libhcoll.so.1']
        for name in ('ldd.txt', 'consumer-ldd.txt', 'gcc14-ldd.txt'):
            text = (output / name).read_text()
            require('not found' not in text and '/opt/mellanox/hcoll' not in text, 'Invalid runtime linkage')
            if name != 'ldd.txt':
                require(all(lib in text for lib in libraries), 'Unexpected linked libraries')
            if not gpu:
                require(not re.search(r'lib(?:cuda|cublas|cusolver)', text), 'CPU variant depends on CUDA')
        loaders = list(output.glob('loader.*'))
        require(len(loaders) == 2, 'Missing MPI-rank loader evidence')
        for path in loaders:
            loaded = re.findall(r'calling init:\s+(\S+)', path.read_text())
            require(all(lib in loaded for lib in libraries), 'Actual loader path differs')
        result['partitions'][partition] = dict(job_id=job, state='COMPLETED', exit_code='0:0',
            elapsed=rows[job][3], node=rows[job][4], prefix=prefix, max_errors=errors,
            lifecycle=traces, gcc14_errors=list(map(float, values[0])),
            numerical_solves=3 + sum(t['numerical_solves'] for t in traces))
    result['numerical_solves'] = sum(p['numerical_solves'] for p in result['partitions'].values())
    write_json(root / ('local-validation.json' if offline else 'validation.json'), result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--submit', action='store_true')
    mode.add_argument('--verify', type=Path)
    mode.add_argument('--inventory', type=Path)
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    if args.inventory:
        print(json.dumps(inventory(args.inventory), indent=2, sort_keys=True))
    elif args.verify:
        verify(args.verify, args.offline)
    else:
        submit()
