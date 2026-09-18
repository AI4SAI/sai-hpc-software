#!/usr/bin/env python3
"""Submit a fresh, short, offline native Spack resolver probe on SAI."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

from cp2k_spack import validate_cache
from remote_controller import container_command, safe_name


def submit(partition, run_id, repair_of=None, diagnosis=None):
    root = Path('/home/stardust/sai-hpc-software')
    control = Path(__file__).resolve().parent
    run = root / 'runs' / safe_name(run_id)
    repairs = 0
    parent = None
    if repair_of:
        if not diagnosis:
            raise ValueError('repair requires a recorded diagnosis')
        parent = root / 'runs' / safe_name(repair_of)
        previous = json.loads((parent / 'request.json').read_text())
        if previous['partition'] != partition:
            raise ValueError('repair partition mismatch')
        repairs = previous['retry_policy'].get('diagnosed_repairs_used', 0) + 1
        if repairs > 2:
            raise ValueError('two diagnosed repair attempts exhausted')
        old_job = str(previous['job_id'])
        accounting = subprocess.check_output(
            ['sacct', '-X', '-j', old_job, '-nP', '-o', 'JobIDRaw,State'], text=True)
        states = [line.split('|')[1] for line in accounting.splitlines()
                  if line.split('|')[0] == old_job]
        if len(states) != 1 or states[0].split()[0] not in {
                'FAILED', 'TIMEOUT', 'CANCELLED', 'NODE_FAIL', 'OUT_OF_MEMORY', 'PREEMPTED'}:
            raise ValueError('repair requires a confirmed terminal failed parent: ' + accounting)
        if (parent / 'repair-child.json').exists():
            raise ValueError('parent already has a repair submission')
    validate_cache(root / 'cache/spack', partition, require_archives=True)
    image = root / 'containers/base/minimal-v1.sif'
    if not image.is_file():
        raise ValueError('missing base image')
    run.mkdir()  # Refuse duplicate submissions or accidental overlay reuse.
    for name in ('runtime', 'results', 'apptainer-cache'):
        (run / name).mkdir()
    command = container_command(
        image, ['/bin/bash', '/control/cp2k_spack_probe.sh', partition],
        overlay=run / 'work.ext3', control=control, jobs=8, gpu=partition == '16V100',
        extra_binds=[(root / 'cache/spack', '/input/spack'),
                     ('/var/lib/dpkg', '/var/lib/dpkg'),
                     ('/etc/os-release', '/etc/os-release'),
                     ('/etc/alternatives', '/etc/alternatives')])
    # Only small result metadata may be written to the host. All extracted
    # source, repositories, stages and the future install tree stay in overlay.
    command[2:2] = ['--bind', str(run / 'results') + ':/results:rw']
    lines = ['#!/bin/bash', '#SBATCH --job-name=cp2k-spack-native',
             '#SBATCH --partition=' + partition,
             '#SBATCH --qos=' + ('flood-1o2gpu' if partition == '16V100' else 'rush-cpu'),
             '#SBATCH --nodes=1', '#SBATCH --ntasks=1', '#SBATCH --time=15',
             '#SBATCH --export=NIL', '#SBATCH --output=' + str(run / 'results/slurm-%j.log')]
    lines += (['#SBATCH --gpus-per-node=1'] if partition == '16V100' else
              ['#SBATCH --cpus-per-task=8', '#SBATCH --mem=16G'])
    lines += ['set -eo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""',
              'source /etc/profile.d/lmod.sh', 'module load apptainer/1.4.4', 'set -u',
              'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
              'export TMPDIR=' + shlex.quote(str(run / 'runtime')),
              'export APPTAINER_TMPDIR="$TMPDIR"',
              'export APPTAINER_CACHEDIR=' + shlex.quote(str(run / 'apptainer-cache')),
              shlex.join(['apptainer', 'overlay', 'create', '--fakeroot', '--sparse', '--size',
                          '4096', str(run / 'work.ext3')]), shlex.join(command)]
    script = run / 'job.sbatch'
    script.write_text('\n'.join(lines) + '\n')
    receipt = {'partition': partition, 'run_id': run_id, 'status': 'prepared',
               'purpose': 'native-concretization-and-mirror-fetch-only',
               'scripts': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in control.iterdir() if p.is_file()},
               'retry_policy': {'incidental_retry_limit': 1, 'diagnosed_repair_limit': 2,
                                'incidental_retries_used': 0, 'diagnosed_repairs_used': repairs}}
    if parent:
        receipt.update(repair_of=repair_of, diagnosis=diagnosis)
        with (parent / 'repair-child.json').open('x') as stream:
            json.dump({'run_id': run_id, 'diagnosis': diagnosis, 'repair_number': repairs}, stream)
    receipt_path = run / 'request.json'
    receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
    job = subprocess.check_output(['sbatch', '--hold', '--parsable', str(script)], text=True).strip().split(';')[0]
    if not job.isdigit():
        raise RuntimeError('unexpected scheduler receipt: ' + job)
    (run / 'job.id').write_text(job + '\n')
    receipt.update(job_id=job, status='submitted-held')
    receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
    subprocess.run(['scontrol', 'release', job], check=True)
    receipt['status'] = 'released'
    receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'job_id': job, 'run': str(run), 'partition': partition}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('partition', choices=['16V100', 'DSPRHBM'])
    parser.add_argument('run_id')
    parser.add_argument('--repair-of')
    parser.add_argument('--diagnosis')
    args = parser.parse_args()
    submit(args.partition, args.run_id, args.repair_of, args.diagnosis)
