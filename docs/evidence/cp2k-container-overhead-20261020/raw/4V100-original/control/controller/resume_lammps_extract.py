#!/usr/bin/env python3
"""Clone a packaging-complete overlay and finish host-side SIF creation."""
import argparse, hashlib, json, shlex, subprocess, sys
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with path.open('rb', buffering=0) as stream:
        while True:
            data = stream.read(16 * 1024 * 1024)
            if not data:
                return h.hexdigest()
            h.update(data)


def run(argv, **kwargs):
    return subprocess.run([str(x) for x in argv], check=True, text=True, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('old_run')
    parser.add_argument('new_run')
    args = parser.parse_args()
    root = Path('/home/stardust/sai-hpc-software')
    old = root / 'experimental/deepmd-lammps/runs' / args.old_run
    new = root / 'experimental/deepmd-lammps/runs' / args.new_run
    if not old.is_dir() or new.exists():
        raise ValueError('old run missing or new run exists')
    old_job = (old / 'job.id').read_text().strip()
    state = run(['sacct', '-X', '-n', '-j', old_job, '-o', 'State'], capture_output=True).stdout.strip()
    if state != 'FAILED':
        raise ValueError('source packaging job is not terminal FAILED')
    log = old / 'results' / f'slurm-{old_job}.log'
    if 'MD_PACKAGING_RECOVERY_COMPLETE' not in log.read_text(errors='replace'):
        raise ValueError('source overlay does not contain a completed packaging marker')
    request = json.loads((old / 'request.json').read_text())
    sys.path.insert(0, request['controller'])
    from md_controller import container_command
    from remote_controller import site_app_binds
    control = Path(request['controller'])
    identity = json.dumps(request['delivery'], sort_keys=True, separators=(',', ':'))
    overlay = old / 'work.ext3'
    source_sha = digest(overlay)
    new.mkdir(mode=0o700)
    for name in ('input', 'results', 'runtime', 'apptainer-cache'):
        (new / name).mkdir(mode=0o700)
    copied = new / 'work.ext3'
    run(['cp', '--sparse=always', '--reflink=auto', '--', overlay, copied])
    if digest(copied) != source_sha:
        raise ValueError('overlay changed during copy')
    image = root / 'containers/base/minimal-v1.sif'
    site_binds = list(site_app_binds('lammps'))
    extract = container_command(image, ['/usr/bin/cat', '/workspace/final.squashfs'],
                                overlay=str(copied) + ':ro', jobs=1, gpu=False)
    verify = container_command(new / 'result.sif',
                               ['/usr/bin/bash', '/control/md_container_entry.sh', 'verify', identity],
                               control=control, jobs=1, gpu=True, extra_binds=site_binds)
    for command in (verify,):
        command[2:2] = ['--bind', str(new / 'runtime') + ':/runtime:rw']
    q = shlex.quote
    jobscript = new / 'job.sbatch'
    jobscript.write_text('\n'.join([
        '#!/bin/bash', '#SBATCH --partition=16V100', '#SBATCH --qos=flood-1o2gpu',
        '#SBATCH --nodes=1', '#SBATCH --ntasks=1', '#SBATCH --gpus-per-node=1',
        '#SBATCH --time=120', '#SBATCH --job-name=md-sif-extract',
        f'#SBATCH --output={new}/results/slurm-%j.log', '#SBATCH --export=NIL',
        'set -eo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""',
        'export USER=${USER:-stardust} LOGNAME=${LOGNAME:-stardust}',
        'source /etc/profile.d/lmod.sh', 'module load apptainer/1.4.4', 'set -u',
        f'export TMPDIR={q(new / "runtime")} APPTAINER_TMPDIR={q(new / "runtime")} APPTAINER_CACHEDIR={q(new / "apptainer-cache")}',
        'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
        shlex.join(extract) + f' > {q(new / "final.squashfs")}',
        f'test -s {q(new / "final.squashfs")}',
        shlex.join(['apptainer', 'sif', 'new', str(new / 'result.sif')]),
        shlex.join(['apptainer', 'sif', 'add', '--datatype', '4', '--partfs', '1', '--parttype', '2', '--partarch', '2', '--groupid', '1', str(new / 'result.sif'), str(new / 'final.squashfs')]),
        shlex.join(verify), 'echo MD_SIF_EXTRACTION_AND_VERIFY_COMPLETE', '']))
    run(['bash', '-n', jobscript])
    run(['sbatch', '--test-only', jobscript])
    job = run(['sbatch', '--parsable', jobscript], capture_output=True).stdout.strip().split(';')[0]
    if not job.isdigit():
        raise ValueError('invalid scheduler receipt')
    (new / 'job.id').write_text(job + '\n')
    request.update(run_id=args.new_run, job=job, job_script_sha256=digest(jobscript),
                   recovery_source=args.old_run, cloned_overlay_sha256=source_sha,
                   packaging_complete=True, scientific_verified=False, published=False)
    (new / 'request.json').write_text(json.dumps(request, sort_keys=True) + '\n')
    print(json.dumps({'job': job, 'run': str(new), 'overlay_sha256': source_sha}))


if __name__ == '__main__':
    main()
