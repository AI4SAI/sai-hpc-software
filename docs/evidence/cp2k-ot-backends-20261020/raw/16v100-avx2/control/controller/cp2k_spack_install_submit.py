#!/usr/bin/env python3
"""Start a dependency installation pilot only after native mirror-fetch passes."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess

from cp2k_spack_seed import checksum
from remote_controller import container_command, safe_name


def submit(probe_id, run_id, repair_of=None, diagnosis=None, repair_mode=None):
    root = Path('/home/stardust/sai-hpc-software')
    probe = root / 'runs' / safe_name(probe_id)
    run = root / 'runs' / safe_name(run_id)
    parent = json.loads((probe / 'request.json').read_text())
    repairs = 0
    failed_run = None
    if repair_mode is not None and not repair_of:
        raise ValueError('repair mode requires a failed parent')
    repair_mode = repair_mode or ('mpi-runtime' if repair_of else 'none')
    if repair_mode not in {'none', 'mpi-runtime', 'bin-tools'}:
        raise ValueError('unknown install repair mode')
    previous = None
    if repair_of:
        if not diagnosis:
            raise ValueError('install repair requires a diagnosis')
        failed_run = root / 'runs' / safe_name(repair_of)
        previous = json.loads((failed_run / 'request.json').read_text())
        if previous.get('probe_run') != probe_id or previous.get('purpose') != 'native-dependency-install-pilot-not-cp2k-build':
            raise ValueError('install repair lineage mismatch')
        repairs = previous['retry_policy']['diagnosed_repairs_used'] + 1
        if repairs > 2:
            raise ValueError('two diagnosed install repairs exhausted')
        failed_job = str(previous['job_id'])
        rows = subprocess.check_output(['sacct', '-X', '-j', failed_job, '-nP', '-o', 'JobIDRaw,State'], text=True).splitlines()
        states = [r.split('|')[1].split()[0] for r in rows if r.split('|')[0] == failed_job]
        if len(states) != 1 or states[0] not in {'FAILED', 'TIMEOUT', 'CANCELLED', 'NODE_FAIL', 'OUT_OF_MEMORY'}:
            raise ValueError('install repair parent is not a terminal failure')
        if (failed_run / 'repair-child.json').exists():
            raise ValueError('install repair already submitted')
        if repair_mode == 'bin-tools' and previous.get('repair_mode') != 'mpi-runtime':
            raise ValueError('bin-tools repair requires the MPI-repaired checkpoint')
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
    recipe_parent = previous if repair_mode == 'bin-tools' else parent
    for name in ('cp2k_spack.py', 'cp2k_spack_environment.py', 'cp2k_spack_native.py', 'environment.sh'):
        if repair_mode == 'mpi-runtime' and name == 'cp2k_spack_environment.py':
            # The in-container repair guard only permits adding the external
            # MPI environment. All source versions/features must remain equal.
            continue
        if checksum(control / name) != recipe_parent.get('scripts', {}).get(name):
            raise ValueError('dependency recipe changed after native probe: ' + name)
    # The probe receipt is tied to its scripts and copied result lock; the
    # install stage additionally checks the exact cloned overlay and its lock.
    if parent.get('purpose') != 'native-concretization-and-mirror-fetch-only':
        raise ValueError('wrong parent operation')
    source_run = failed_run if repair_mode == 'bin-tools' else probe
    overlay = source_run / 'work.ext3'
    if overlay.resolve() != overlay or not overlay.is_file():
        raise ValueError('invalid successful probe overlay')
    if (not repair_of and (probe / 'install-child.json').exists()) or run.exists():
        raise ValueError('dependency pilot already submitted')
    source_hash = checksum(overlay)
    lock_hash = checksum(source_run / 'results/spack.lock')
    if repair_mode == 'bin-tools':
        if lock_hash != (source_run / 'results/repaired-lock.sha256').read_text().strip():
            raise ValueError('MPI-repaired checkpoint lock changed')
    run.mkdir()
    for name in ('runtime', 'results', 'apptainer-cache'):
        (run / name).mkdir()
    extra_binds = [(root / 'cache/spack', '/input/spack'),
                   ('/var/lib/dpkg', '/var/lib/dpkg'), ('/etc/os-release', '/etc/os-release'),
                   ('/etc/alternatives', '/etc/alternatives')]
    if repair_mode == 'bin-tools':
        # The minimal base has only bash/sh under /bin. Libint's makefiles
        # hard-code /bin/rm. Bind the existing node tools read-only; do not
        # patch the pinned source or replace the live/failed base image.
        extra_binds.append(('/usr/bin', '/bin'))
    command = container_command(
        root / 'containers/base/minimal-v1.sif',
        ['/bin/bash', '/control/cp2k_spack_install.sh', partition, lock_hash, repair_mode],
        overlay=run / 'work.ext3', control=control, jobs=8, gpu=partition == '16V100',
        extra_binds=extra_binds)
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
              shlex.join(['printf', '%s  %s\n', source_hash, str(run / 'work.ext3')]) + ' | sha256sum --check --status']
    if repair_mode == 'bin-tools':
        # The 4-GiB image was sized for source/solver preparation, not all
        # generated Libint code plus PLUMED objects. Grow only the verified,
        # unmounted NEW clone; this is headroom, not the diagnosed failure.
        lines += [shlex.join(['/usr/sbin/e2fsck', '-pf', str(run / 'work.ext3')]) + ' || test "$?" -eq 1',
                  shlex.join(['/usr/sbin/resize2fs', str(run / 'work.ext3'), '16G'])]
    lines.append(shlex.join(command))
    script = run / 'job.sbatch'
    script.write_text('\n'.join(lines) + '\n')
    subprocess.run(['bash', '-n', str(script)], check=True)
    request = {'purpose': 'native-dependency-install-pilot-not-cp2k-build',
               'probe_run': probe_id, 'probe_job': old_job, 'run_id': run_id, 'partition': partition,
               'source_run': source_run.name,
               'source_overlay_sha256': source_hash, 'lock_sha256': lock_hash,
               'script_sha256': checksum(script),
               'scripts': {p.name: checksum(p) for p in control.iterdir() if p.is_file()},
               'retry_policy': {'incidental_retry_limit': 1, 'diagnosed_repair_limit': 2,
                                'incidental_retries_used': 0, 'diagnosed_repairs_used': repairs},
               'status': 'prepared'}
    if repair_of:
        request.update(repair_of=repair_of, diagnosis=diagnosis, repair_mode=repair_mode)
    if repair_mode == 'bin-tools':
        request['cloned_overlay_capacity_bytes'] = 16 * 1024**3
    request_path = run / 'request.json'
    request_path.write_text(json.dumps(request, indent=2) + '\n')
    lineage = failed_run / 'repair-child.json' if failed_run else probe / 'install-child.json'
    with lineage.open('x') as stream:
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
    parser.add_argument('--repair-of')
    parser.add_argument('--diagnosis')
    parser.add_argument('--repair-mode', choices=('mpi-runtime', 'bin-tools'))
    args = parser.parse_args()
    submit(args.probe_run, args.run_id, args.repair_of, args.diagnosis, args.repair_mode)
