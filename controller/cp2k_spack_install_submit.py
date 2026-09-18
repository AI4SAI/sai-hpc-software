#!/usr/bin/env python3
"""Start a dependency installation pilot only after native mirror-fetch passes."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess

from cp2k_spack_seed import checksum
from remote_controller import container_command, safe_name


def submit(probe_id, run_id):
    root = Path('/home/stardust/sai-hpc-software')
    probe = root / 'runs' / safe_name(probe_id)
    run = root / 'runs' / safe_name(run_id)
    parent = json.loads((probe / 'request.json').read_text())
    partition = parent['partition']
    if partition not in ('16V100', 'DSPRHBM'):
        raise ValueError('unsupported native pilot partition')
    old_job = str(parent['job_id'])
    rows = subprocess.check_output(['sacct', '-X', '-j', old_job, '-nP', '-o',
                                    'JobIDRaw,State,ExitCode'], text=True).splitlines()
    if [r.split('|')[1:3] for r in rows if r.split('|')[0] == old_job] != [['COMPLETED', '0:0']]:
        raise ValueError('native probe is not a completed successful job')
    success = json.loads((probe / 'results/probe-success.json').read_text())
    if (success['partition'] != partition or success.get('mirror_only') is not True or
            success.get('installed') is not False or not success.get('fetched')):
        raise ValueError('no successful independent native fetch evidence')
    control = Path(__file__).resolve().parent
    for name in ('cp2k_spack.py', 'cp2k_spack_environment.py', 'cp2k_spack_native.py', 'environment.sh'):
        if checksum(control / name) != parent.get('scripts', {}).get(name):
            raise ValueError('dependency recipe changed after native probe: ' + name)
    # The probe receipt is tied to its scripts and copied result lock; the
    # install stage additionally checks the exact cloned overlay and its lock.
    if parent.get('purpose') != 'native-concretization-and-mirror-fetch-only':
        raise ValueError('wrong parent operation')
    overlay = probe / 'work.ext3'
    if overlay.resolve() != overlay or not overlay.is_file():
        raise ValueError('invalid successful probe overlay')
    if (probe / 'install-child.json').exists() or run.exists():
        raise ValueError('dependency pilot already submitted')
    source_hash = checksum(overlay)
    lock_hash = checksum(probe / 'results/spack.lock')
    run.mkdir()
    for name in ('runtime', 'results', 'apptainer-cache'):
        (run / name).mkdir()
    command = container_command(
        root / 'containers/base/minimal-v1.sif',
        ['/bin/bash', '/control/cp2k_spack_install.sh', partition, lock_hash],
        overlay=run / 'work.ext3', control=control, jobs=8, gpu=partition == '16V100',
        extra_binds=[(root / 'cache/spack', '/input/spack'),
                     ('/var/lib/dpkg', '/var/lib/dpkg'), ('/etc/os-release', '/etc/os-release'),
                     ('/etc/alternatives', '/etc/alternatives')])
    command[2:2] = ['--bind', str(run / 'results') + ':/results:rw']
    lines = ['#!/bin/bash', '#SBATCH --job-name=cp2k-spack-deps',
             '#SBATCH --partition=' + partition,
             '#SBATCH --qos=' + ('flood-1o2gpu' if partition == '16V100' else 'rush-cpu'),
             '#SBATCH --nodes=1', '#SBATCH --ntasks=1', '#SBATCH --time=240',
             '#SBATCH --export=NIL', '#SBATCH --output=' + str(run / 'results/slurm-%j.log')]
    lines += (['#SBATCH --gpus-per-node=1'] if partition == '16V100' else
              ['#SBATCH --cpus-per-task=8', '#SBATCH --mem=28G'])
    lines += ['set -eo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""',
              'source /etc/profile.d/lmod.sh', 'module load apptainer/1.4.4', 'set -u',
              'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
              'export TMPDIR=' + shlex.quote(str(run / 'runtime')),
              'export APPTAINER_TMPDIR="$TMPDIR"',
              'export APPTAINER_CACHEDIR=' + shlex.quote(str(run / 'apptainer-cache')),
              'test ! -e ' + shlex.quote(str(run / 'work.ext3')),
              shlex.join(['cp', '--sparse=always', '--reflink=auto', '--', str(overlay), str(run / 'work.ext3')]),
              shlex.join(['printf', '%s  %s\n', source_hash, str(run / 'work.ext3')]) + ' | sha256sum --check --status',
              shlex.join(command)]
    script = run / 'job.sbatch'
    script.write_text('\n'.join(lines) + '\n')
    subprocess.run(['bash', '-n', str(script)], check=True)
    request = {'purpose': 'native-dependency-install-pilot-not-cp2k-build',
               'probe_run': probe_id, 'probe_job': old_job, 'run_id': run_id, 'partition': partition,
               'source_overlay_sha256': source_hash, 'lock_sha256': lock_hash,
               'script_sha256': checksum(script),
               'scripts': {p.name: checksum(p) for p in control.iterdir() if p.is_file()},
               'retry_policy': {'incidental_retry_limit': 1, 'diagnosed_repair_limit': 2,
                                'incidental_retries_used': 0, 'diagnosed_repairs_used': 0},
               'status': 'prepared'}
    request_path = run / 'request.json'
    request_path.write_text(json.dumps(request, indent=2) + '\n')
    with (probe / 'install-child.json').open('x') as stream:
        json.dump({'run_id': run_id, 'new_stage': 'dependency-install'}, stream)
    job = subprocess.check_output(['sbatch', '--hold', '--parsable', str(script)], text=True).strip().split(';')[0]
    if not job.isdigit():
        raise ValueError('invalid job receipt')
    (run / 'job.id').write_text(job + '\n')
    request.update(job_id=job, status='submitted-held')
    request_path.write_text(json.dumps(request, indent=2) + '\n')
    subprocess.run(['scontrol', 'release', job], check=True)
    request['status'] = 'released'
    request_path.write_text(json.dumps(request, indent=2) + '\n')
    print(json.dumps({'job_id': job, 'run': str(run), 'partition': partition}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe_run')
    parser.add_argument('run_id')
    args = parser.parse_args()
    submit(args.probe_run, args.run_id)
