#!/usr/bin/env python3
"""Submit a fresh LTS smoke check after a build, retaining every attempt."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('build')
    parser.add_argument('--attempt', type=int, default=1)
    args = parser.parse_args()
    root = Path('/org/org-open-sai/proj-sai-hpc-software')
    build = Path(args.build).resolve()
    if build.parent != root / 'runs' or not 1 <= args.attempt <= 9:
        parser.error('project build and attempt 1..9 required')
    request = json.loads((build / 'request.json').read_text())
    identity, control = request['identity'], Path(request['controller'])
    if identity['source_ref'] != 'LTS':
        parser.error('LTS build required')
    target = identity['target']
    job = (build / 'job.id').read_text().strip()
    if not job.isdigit():
        raise ValueError('invalid build receipt')
    accounting = subprocess.check_output(
        ['sacct', '-X', '-j', job, '-n', '-P', '-o', 'JobIDRaw,State,ExitCode'], text=True)
    rows = [line.split('|') for line in accounting.splitlines() if line.split('|')[0] == job]
    if len(rows) != 1:
        raise ValueError('build accounting record unavailable')
    state, exit_code = rows[0][1:3]
    if state == 'COMPLETED' and exit_code == '0:0':
        # Slurm can discard completed jobs before a later check is submitted.
        # The monitor below still verifies the build and artifact provenance.
        dependency = []
    elif state in ('PENDING', 'RUNNING', 'CONFIGURING', 'COMPLETING'):
        dependency = [f'#SBATCH --dependency=afterok:{job}', '#SBATCH --kill-on-invalid-dep=yes']
    else:
        raise ValueError(f'build cannot be checked: {state} ({exit_code})')
    suffix = '' if args.attempt == 1 else f'-r{args.attempt}'
    task = root / 'runtime-tests' / (build.name + '-lts-science' + suffix)
    task.mkdir()
    for name in ('results', 'mpi-runtime', 'apptainer-runtime'):
        (task / name).mkdir()
    checker = task / 'check.py'
    checker.write_bytes(Path(__file__).with_name('check.py').read_bytes())
    checker.chmod(0o444)
    gpu = identity['gpus'] > 0
    qos = ('rush-1o2gpu' if target in ('4v100-avx512', '8v100v0-avx512') else
           'flood-1o2gpu' if gpu else 'rush-cpu')
    lines = ['#!/bin/bash', f'#SBATCH --job-name=lts-science-{target}',
             f'#SBATCH --partition={identity["partition"]}', f'#SBATCH --qos={qos}',
             '#SBATCH --account=proj-sai-hpc-software', f'#SBATCH --chdir={task}',
             '#SBATCH --nodes=1', '#SBATCH --ntasks=1', '#SBATCH --time=20',
             f'#SBATCH --output={task}/results/slurm-%j.log', '#SBATCH --export=NIL']
    lines += dependency
    lines += (['#SBATCH --gpus-per-node=1'] if gpu else
              ['#SBATCH --cpus-per-task=1', '#SBATCH --mem=6G', '#SBATCH --exclude=d9470n07'])
    lines += ['set -eo pipefail', 'export PATH=/usr/bin:/bin',
              'export LD_LIBRARY_PATH="" LD_PRELOAD=""',
              'export USER=${SLURM_JOB_USER:?}', 'export LOGNAME=$USER',
              f'export TMPDIR={task}/mpi-runtime', f'export APPTAINER_TMPDIR={task}/apptainer-runtime',
              f'export APPTAINER_CACHEDIR={task}/apptainer-runtime',
              'source /etc/profile.d/lmod.sh', 'module purge',
              'module load apptainer/1.4.4 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto', 'set -u',
              f'export SAI_SOFTWARE_ROOT={root}',
              shlex.join(['python3', str(control / 'software_controller.py'), 'monitor',
                          build.name, '--timeout', '60', '--interval', '1']),
              shlex.join(['python3', str(checker), str(build), '--attempt', str(args.attempt)])]
    script = task / 'job.sbatch'
    script.write_text('\n'.join(lines) + '\n')
    script.chmod(0o700)
    subprocess.run(['bash', '-n', str(script)], check=True)
    result = subprocess.run(['sbatch', '--parsable', str(script)], text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('Slurm submission failed: ' + result.stderr.strip())
    science_job = result.stdout.strip().split(';')[0]
    if not science_job.isdigit():
        raise ValueError('invalid science job receipt')
    (task / 'job.id').write_text(science_job + '\n')
    record = dict(build=str(build), build_job=job, identity=identity, job=science_job,
                  checker_sha256=hashlib.sha256(checker.read_bytes()).hexdigest(),
                  script_sha256=hashlib.sha256(script.read_bytes()).hexdigest(),
                  scope='One MPI rank: PW, HSE, DeePKS and GPU cuSOLVERMp')
    (task / 'request.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(dict(target=target, job=science_job, task=str(task))))


if __name__ == '__main__':
    main()
