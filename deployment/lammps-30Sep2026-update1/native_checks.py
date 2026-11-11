#!/usr/bin/env python3
"""Verify native update1 using the accepted SIF's cases, tolerances and inventory."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import shutil
import subprocess
import sys
import time

sys.dont_write_bytecode = True
from runtime_files import digest, plain, require, verify_records

PROJECT = Path('/org/org-open-sai/proj-sai-hpc-software')
PACKAGE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True); stream.write('\n')


def context(output):
    require(digest(output / 'request.json') == (output / 'request.sha256').read_text().strip(), 'request changed')
    request = read(output / 'request.json')
    for name, checksum in request['files'].items():
        path = output / name
        plain(path)
        require(digest(path) == checksum, 'frozen input changed: ' + name)
    require(digest(Path(request['module'])) == request['module_sha256'], 'tested module changed')
    verify_records(Path(request['prefix']), {r['path']: r for r in request['spec']['files']})
    dep = request['dependency']
    verify_records(Path(dep['identity']['install_prefix']), {r['path']: r for r in dep['files']})
    return request


def submit(root, parts):
    import install as deployment
    release = deployment.load_release()
    deployment.preflight(release, root, installed=True)
    account = pwd.getpwuid(os.getuid())
    work = PROJECT / 'operations' / ('lammps-update1-native-' + datetime.now().strftime('%Y%m%d-%H%M%S')
                                    + '-' + str(os.getpid()))
    work.mkdir()
    jobs = []
    for spec in release['deliveries']:
        part = spec['partition']
        if part not in parts:
            continue
        output = work / part
        output.mkdir()
        source = Path(spec['science'])
        original = read(source / 'request.json')
        pins = {}

        def copy(path, name, expected):
            require(digest(path) == expected, 'accepted source changed: ' + str(path))
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target); target.chmod(0o444)
            pins[name] = expected

        for name, checksum in original['files'].items():
            if name.startswith(('helpers/', 'inputs/')) or name == 'reference.json':
                copy(source / name, name, checksum)
        copy(PACKAGE / spec['acceptance'], 'sif-acceptance.json', spec['acceptance_sha256'])
        for name in ('native_checks.py', 'native-rank.sh', 'runtime_files.py'):
            copy(PACKAGE / name, 'control/' + name, digest(PACKAGE / name))
        module = root / deployment.MODULE.relative_to('/')
        dep = read(PACKAGE / spec['dependency_records'])
        request = dict(spec=spec, prefix=str(root / spec['prefix'].lstrip('/')), dependency=dep,
                       module=str(module), module_sha256=digest(module), files=pins,
                       route='physical-opt' if root == Path('/') else 'staged-native',
                       release_sha256=digest(PACKAGE / 'release.json'))
        save(output / 'request.json', request)
        (output / 'request.sha256').write_text(digest(output / 'request.json') + '\n')
        runtime = PROJECT / 'runtime' / ('lmpu1-' + str(os.getpid()) + '-' + part)
        runtime.mkdir(parents=True, mode=0o700)
        script = '\n'.join([
            '#!/bin/bash', '#SBATCH --nodes=2', '#SBATCH --ntasks=2', '#SBATCH --ntasks-per-node=1',
            '#SBATCH --gpus-per-node=1', '#SBATCH --time=00:30:00', '#SBATCH --export=NIL',
            '#SBATCH --account=proj-sai-hpc-software', '#SBATCH --partition=' + part,
            '#SBATCH --qos=' + ('flood-1o2gpu' if part == '16V100' else 'rush-1o2gpu'),
            '#SBATCH --chdir=' + str(output), '#SBATCH --output=' + str(output / 'slurm-%j.log'),
            'set -eo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" PYTHONPATH="" LD_PRELOAD=""',
            'export HOME=' + shlex.quote(account.pw_dir) + ' USER=' + shlex.quote(account.pw_name)
            + ' LOGNAME=' + shlex.quote(account.pw_name),
            'unset PYTHONHOME', 'source /etc/profile.d/lmod.sh', 'module purge',
            'module use ' + shlex.quote(str(module.parent.parent)),
            'module load lammps/' + deployment.MODULE.name, 'set -u',
            'export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1',
            'export DP_INTRA_OP_PARALLELISM_THREADS=1 DP_INTER_OP_PARALLELISM_THREADS=1',
            'export TF_NUM_INTRAOP_THREADS=1 TF_NUM_INTEROP_THREADS=1',
            'export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 SLURM_EXPORT_ENV=ALL',
            'export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher',
            'export TMPDIR=' + shlex.quote(str(runtime)),
            'export TMP="$TMPDIR" TEMP="$TMPDIR" XDG_CACHE_HOME="$TMPDIR" CUDA_CACHE_PATH="$TMPDIR"',
            'scontrol show hostnames "$SLURM_JOB_NODELIST" > nodes.txt',
            'module -t list > modules.txt 2>&1',
            '/usr/bin/python3 -B control/native_checks.py run "$PWD"',
            'module unload lammps/' + deployment.MODULE.name,
            '[[ -z "${SAI_LAMMPS_PREFIX:-}" && -z "${SAI_LAMMPS_NATIVE_PARTITION:-}" ]]',
            'touch unload.passed', ''])
        (output / 'job.sbatch').write_text(script)
        subprocess.run(['bash', '-n', str(output / 'job.sbatch')], check=True)
        job = subprocess.check_output(['sbatch', '--parsable', '--hold', str(output / 'job.sbatch')], text=True).strip().split(';')[0]
        require(re.fullmatch('[1-9][0-9]*', job), 'invalid job')
        (output / 'job.id').write_text(job + '\n')
        save(output / 'submission.json', dict(job=job, script_sha256=digest(output / 'job.sbatch'),
                                             request_sha256=digest(output / 'request.json')))
        subprocess.run(['scontrol', 'release', job], check=True)
        jobs.append(dict(partition=part, job=job, directory=str(output)))
        print(json.dumps(jobs[-1]), flush=True)
    save(work / 'jobs.json', jobs)
    print('Results: ' + str(work))


def helpers(output):
    sys.path.insert(0, str(output / 'helpers'))
    import md_evidence, md_science, gpu_functional_smoke, md_probe
    return md_evidence, md_science, gpu_functional_smoke, md_probe


def run(output):
    request = context(output)
    spec = request['spec']
    require(os.environ['SLURM_JOB_ID'] == (output / 'job.id').read_text().strip()
            and os.environ['SLURM_JOB_PARTITION'] == spec['partition']
            and os.environ['SLURM_JOB_ACCOUNT'] == 'proj-sai-hpc-software', 'wrong allocation')
    require(os.environ.get('SAI_LAMMPS_PREFIX') == request['prefix']
            and shutil.which('lmp') == request['prefix'] + '/bin/lmp', 'wrong native module')
    mpi = '/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-' + spec['identity']['dependency_isa']
    require(shutil.which('mpirun') == mpi + '/bin/mpirun', 'wrong host MPI')
    hosts = (output / 'nodes.txt').read_text().split()
    require(len(hosts) == len(set(hosts)) == 2, 'need two different nodes')
    _, science, _, _ = helpers(output)
    ref = read(output / 'reference.json')
    results = output / 'results'; results.mkdir()

    def execute(name, count, argv, prepare=None):
        directory = results / name; directory.mkdir(); (directory / 'ranks').mkdir()
        if prepare:
            prepare(directory)
        command = [mpi + '/bin/mpirun', '-np', str(count), '--host', ','.join(hosts[:count]),
                   '--map-by', 'ppr:1:node', '--bind-to', 'none', 'bash', str(output / 'control/native-rank.sh'), *argv]
        with (directory / 'stdout.txt').open('x') as stream:
            start = time.monotonic()
            result = subprocess.run(command, cwd=directory, stdout=stream, stderr=subprocess.STDOUT, timeout=600)
        save(directory / 'process.json', dict(command=command, returncode=result.returncode,
                                              seconds=time.monotonic()-start, job=os.environ['SLURM_JOB_ID']))
        require(result.returncode == 0, 'native case failed: ' + name)
        print(name + ': completed', flush=True)

    execute('version', 2, ['-h'])
    subprocess.run([request['dependency']['identity']['install_prefix'] + '/bin/python', '-B',
                    str(output / 'control/native_checks.py'), 'inventory', str(output)], check=True)
    for backend in ('tf', 'pt', 'jax'):
        fixture = science._load_fixture(output / 'inputs/science', backend)
        for mode, count in (('single', 1), ('multi', 2)):
            execute('deepmd-' + backend + '-' + mode, count, ['-in', 'in.' + backend, '-log', 'log.lammps'],
                    lambda dest: science.copy_trial_inputs(output / 'inputs/science', dest, backend, fixture))
    for name, case in ref['gpu'].items():
        options = (['-pk', 'gpu', '1', 'split', '1.0'] if case['mode'] == 'gpu' else
                   ['-k', 'on', 'g', '1', 't', '1', '-sf', 'kk', '-pk', 'kokkos', 'gpu/aware', 'off'])
        def prepare(dest):
            for filename in ('data.lmp', 'in.lmp'):
                shutil.copyfile(output / 'inputs/gpu' / name / filename, dest / filename)
        for mode, count in (('single', 1), ('multi', 2)):
            execute(name + '-' + mode, count, [*options, '-in', 'in.lmp', '-log', 'log.lammps'], prepare)
    report = verify_raw(output)
    report['files'] = {str(p.relative_to(output)): digest(p) for p in results.rglob('*') if p.is_file()}
    save(output / 'native-acceptance.json', report)


def inventory(output):
    request = context(output)
    _, _, _, probe = helpers(output)
    save(output / 'results/features.json', probe.lammps_inventory(request['prefix']))


def verify_raw(output):
    request = context(output)
    evidence, science, gpu, _ = helpers(output)
    ref, accepted = read(output / 'reference.json'), read(output / 'sif-acceptance.json')
    require(ref['tolerances'] == science.TOLERANCES and ref['gpu_tolerances'] == dict(atol=2e-6, rtol=2e-5),
            'original tolerances changed')
    features = read(output / 'results/features.json')
    for key in ('version', 'packages', 'styles', 'library_sha256'):
        require(features[key] == accepted['features'][key], 'native feature/library mismatch: ' + key)
    require(features['library'].startswith(request['prefix'] + '/') and 'not found' not in features['ldd'],
            'wrong native library')
    cases = set(accepted['samples']) | {'version'}
    require({p.name for p in (output / 'results').iterdir() if p.is_dir()} == cases, 'incomplete native matrix')
    hosts = (output / 'nodes.txt').read_text().split()
    require(len(hosts) == len(set(hosts)) == 2, 'need two distinct nodes')
    binary = request['prefix'] + '/bin/lmp'
    mpi = '/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-' + request['spec']['identity']['dependency_isa']
    samples = {}
    for name in sorted(cases):
        folder = output / 'results' / name
        process = read(folder / 'process.json')
        require(process['returncode'] == 0 and process['job'] == (output / 'job.id').read_text().strip(), 'failed native process')
        count = 1 if name.endswith('-single') else 2
        command = [mpi + '/bin/mpirun', '-np', str(count), '--host', ','.join(hosts[:count]),
                   '--map-by', 'ppr:1:node', '--bind-to', 'none', 'bash', str(output / 'control/native-rank.sh')]
        require(process['command'][:len(command)] == command, 'wrong native MPI launch')
        args = process['command'][len(command):]
        require(len(list((folder / 'ranks').glob('rank-*.tsv'))) == count, 'incomplete ranks')
        for rank, host in enumerate(hosts[:count]):
            fields = (folder / 'ranks' / ('rank-' + str(rank) + '.tsv')).read_text().strip().split('\t')
            require(len(fields) == 6 and fields[:3] == [str(rank), host, binary]
                    and fields[3] not in ('', '-1') and fields[4] and fields[5] == digest(Path(binary)), 'wrong native rank')
            if name == 'version':
                loader = (folder / 'ranks' / ('ldd-' + str(rank) + '.txt')).read_text()
                require('not found' not in loader and mpi + '/lib/libmpi.so.40' in loader
                        and request['dependency']['identity']['install_prefix'] + '/lib/libdeepmd' in loader,
                        'wrong native MPI/DeePMD loader')
        stdout = (folder / 'stdout.txt').read_text()
        if name == 'version':
            require(args == ['-h'] and '30 Sep 2026 - Update 1' in stdout, 'wrong update1 banner')
            continue
        if name.startswith('deepmd-'):
            backend = name.split('-')[1]
            require(args == ['-in', 'in.' + backend, '-log', 'log.lammps'], 'wrong DeePMD backend command')
            for filename, checksum in ref['case_files'].items():
                if filename in ('data.lmp', 'plumed.dat', 'in.' + backend) or filename.split('/')[0] == science.BACKENDS[backend]:
                    require(digest(folder / filename) == checksum, 'science fixture changed')
            observed = science.parse_lammps_output(stdout, (folder / 'result.dump').read_text())
            fixture = science._load_fixture(output / 'inputs/science', backend)
            science.verify_plumed_output((folder / 'COLVAR').read_text(), fixture['distance_angstrom'])
            for field, tolerance in ref['tolerances'].items():
                evidence._compare_numeric(observed[field], accepted['samples'][name][field], **tolerance, path=name+'/'+field)
        else:
            case_name = name.rsplit('-', 1)[0]
            case = ref['gpu'][case_name]
            options = (['-pk', 'gpu', '1', 'split', '1.0'] if case['mode'] == 'gpu' else
                       ['-k', 'on', 'g', '1', 't', '1', '-sf', 'kk', '-pk', 'kokkos', 'gpu/aware', 'off'])
            require(args == [*options, '-in', 'in.lmp', '-log', 'log.lammps'], 'wrong GPU command')
            for filename in ('data.lmp', 'in.lmp'):
                require(digest(folder / filename) == digest(output / 'inputs/gpu' / case_name / filename), 'GPU fixture changed')
            marker = '- Using acceleration for' if case['mode'] == 'gpu' else 'KOKKOS mode'
            require(marker in stdout, 'GPU execution marker missing')
            observed = list(gpu.values(folder))
            evidence._compare_numeric(observed, accepted['samples'][name], **ref['gpu_tolerances'], path=name)
        samples[name] = observed
    return dict(passed=True, route=request['route'], prefix=request['prefix'], samples=samples,
                features=features, job=(output / 'job.id').read_text().strip(),
                partition=request['spec']['partition'], request_sha256=digest(output / 'request.json'))


def verify(work):
    reports = []
    for row in read(work / 'jobs.json'):
        output = Path(row['directory'])
        submission = read(output / 'submission.json')
        require(submission == dict(job=row['job'], script_sha256=digest(output / 'job.sbatch'),
                                   request_sha256=digest(output / 'request.json')), 'submission changed')
        accounting = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', row['job'],
             '-o', 'JobIDRaw,State,ExitCode,Partition,Account,WorkDir'], text=True)
        record, = [line.split('|') for line in accounting.splitlines() if line.split('|')[0] == row['job']]
        require(record[1:6] == ['COMPLETED', '0:0', row['partition'], 'proj-sai-hpc-software', str(output)],
                'native job is not successfully complete: ' + accounting)
        require((output / 'unload.passed').is_file(), 'module unload did not pass')
        report = read(output / 'native-acceptance.json')
        observed = verify_raw(output)
        observed['files'] = {str(p.relative_to(output)): digest(p) for p in (output / 'results').rglob('*') if p.is_file()}
        require(report == observed, 'raw native evidence changed')
        reports.append(dict(partition=row['partition'], job=row['job'], passed=True, route=report['route'],
                            cases=len(report['samples']), acceptance_sha256=digest(output / 'native-acceptance.json')))
    print(json.dumps(dict(passed=True, results=reports), indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('submit', 'run', 'inventory', 'verify'))
    parser.add_argument('directory', nargs='?', type=Path)
    parser.add_argument('--stage-root', type=Path, default=Path('/'))
    parser.add_argument('--partition', choices=('all', '4V100', '16V100', '8V100V0'), default='all')
    args = parser.parse_args()
    if args.operation == 'submit':
        submit(args.stage_root, {'4V100', '16V100', '8V100V0'} if args.partition == 'all' else {args.partition})
    else:
        require(args.directory is not None, 'results directory required')
        globals()[args.operation](args.directory)
