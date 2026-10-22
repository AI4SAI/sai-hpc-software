#!/usr/bin/env python3
"""Extract an already packed filesystem read-only; never clone or mount its ext3."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys


def q(value):
    return shlex.quote(str(value))


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def render(new, old, verify):
    overlay = old / 'work.ext3'
    output = new / 'final.squashfs'
    # debugfs without -w opens the image read-only. This avoids fuse2fs mounting
    # and shutdown issues, and does not rewrite the source filesystem metadata.
    return '\n'.join([
        '#!/bin/bash', '#SBATCH --partition=16V100', '#SBATCH --qos=flood-1o2gpu',
        '#SBATCH --nodes=1', '#SBATCH --ntasks=1', '#SBATCH --gpus-per-node=1',
        '#SBATCH --time=45', '#SBATCH --job-name=md-sif-finalize', '#SBATCH --export=NIL',
        f'#SBATCH --output={new}/results/slurm-%j.log', 'set -eo pipefail',
        'export PATH=/usr/sbin:/usr/bin:/sbin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""',
        'export USER=${USER:-stardust} LOGNAME=${LOGNAME:-stardust}',
        'source /etc/profile.d/lmod.sh', 'module load apptainer/1.4.4', 'set -u',
        f'export TMPDIR={q(new / "runtime")} APPTAINER_TMPDIR={q(new / "runtime")} APPTAINER_CACHEDIR={q(new / "apptainer-cache")}',
        'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
        f'stat -c "%s %Y %Z" {q(overlay)} > {q(new / "results/source.before")}',
        f'test ! -e {q(output)}',
        shlex.join(['/usr/sbin/debugfs', '-R', f'dump /upper/workspace/final.squashfs {output}', str(overlay)]),
        f'test -s {q(output)}',
        f'/usr/bin/unsquashfs -s {q(output)}',
        f'stat -c "%s %Y %Z" {q(overlay)} > {q(new / "results/source.after")}',
        f'cmp {q(new / "results/source.before")} {q(new / "results/source.after")}',
        f'sha256sum {q(output)} > {q(new / "results/squashfs.sha256")}',
        shlex.join(['apptainer', 'sif', 'new', str(new / 'result.sif')]),
        shlex.join(['apptainer', 'sif', 'add', '--datatype', '4', '--partfs', '1', '--parttype', '2',
                    '--partarch', '2', '--groupid', '1', str(new / 'result.sif'), str(output)]),
        'echo MD_SIF_CREATED_NOT_YET_VERIFIED', shlex.join(verify),
        f'sha256sum {q(new / "result.sif")} > {q(new / "results/artifact.sha256")}',
        'echo MD_SIF_VERIFY_PASSED_SCIENCE_PENDING', ''])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('old_run')
    parser.add_argument('new_run')
    args = parser.parse_args()
    for value in (args.old_run, args.new_run):
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}', value):
            raise ValueError('unsafe run name')
    root = Path('/home/stardust/sai-hpc-software/experimental/deepmd-lammps/runs')
    old, new = root / args.old_run, root / args.new_run
    if new.exists() or old.resolve() != old or (old / 'work.ext3').is_symlink():
        raise ValueError('unsafe or existing run')
    old_job = (old / 'job.id').read_text().strip()
    if not old_job.isdigit():
        raise ValueError('bad source job id')
    state = subprocess.check_output(['sacct', '-X', '-n', '-j', old_job, '-o', 'State'], text=True).strip()
    if state != 'FAILED':
        raise ValueError('source packaging job must be terminal failed')
    if 'MD_PACKAGING_RECOVERY_COMPLETE' not in (old / f'results/slurm-{old_job}.log').read_text():
        raise ValueError('source export did not finish')
    request = json.loads((old / 'request.json').read_text())
    control = Path(request['controller'])
    sys.path.insert(0, str(control))
    from remote_controller import container_command, site_app_binds
    from md_tracking import fingerprint
    if fingerprint(control) != request['delivery']['recipe_sha256']:
        raise ValueError('frozen build controller has changed')
    identity = json.dumps(request['delivery'], sort_keys=True, separators=(',', ':'))
    wrapper = Path(__file__).with_name('md_container_entry_verify_patched.sh')
    audit = Path(__file__).with_name('md_relocate_audit.py')
    if not wrapper.is_file() or not audit.is_file():
        raise ValueError('verification diagnostic helpers are missing')
    verify = container_command(new / 'result.sif',
        ['/usr/bin/env', 'PYTHONDONTWRITEBYTECODE=1', '/usr/bin/bash',
         '/verify-patched.sh', 'verify', identity],
        control=control, jobs=1, gpu=True,
        extra_binds=[*site_app_binds('lammps'),
                     (wrapper, '/verify-patched.sh'),
                     (audit, '/audit-patched.py')])
    # Verification only reads the SIF and installed dependencies.  Fakeroot
    # is needed while creating the original ext3 overlay, but it is not
    # needed for a read-only SIF; with it enabled Apptainer injects
    # /.singularity.d/libs/libfakeroot.so into ldd output, which is an
    # internal launcher library rather than a relocatable site dependency.
    verify = [arg for arg in verify if arg != '--fakeroot']
    verify[2:2] = ['--bind', str(new / 'runtime') + ':/runtime:rw']
    script = render(new, old, verify)
    subprocess.run(['bash', '-n'], input=script, text=True, check=True)
    new.mkdir(mode=0o700)
    for name in ('results', 'runtime', 'apptainer-cache'):
        (new / name).mkdir(mode=0o700)
    jobscript = new / 'job.sbatch'
    jobscript.write_text(script)
    subprocess.run(['sbatch', '--test-only', jobscript], check=True)
    job = subprocess.check_output(['sbatch', '--hold', '--parsable', jobscript], text=True).strip().split(';')[0]
    if not job.isdigit():
        raise ValueError('ambiguous job receipt')
    # Keep the original request under a separate name; do not silently relabel
    # stage job ids or reuse old success receipts in the recovery request.
    (new / 'source-request.json').write_bytes((old / 'request.json').read_bytes())
    record = dict(run_id=args.new_run, job=job, controller=str(control), delivery=request['delivery'],
                  source_run=str(old), source_job=old_job, source_request_sha256=digest(old / 'request.json'),
                  helper_sha256=digest(Path(__file__)), job_script_sha256=digest(jobscript),
                  source_overlay_open_mode='debugfs read-only, no mount',
                  build_verified=False, scientific_verified=False, published=False)
    (new / 'request.json').write_text(json.dumps(record, sort_keys=True) + '\n')
    (new / 'job.id').write_text(job + '\n')
    subprocess.run(['scontrol', 'release', job], check=True)
    print(json.dumps({'job': job, 'run': str(new)}))


if __name__ == '__main__':
    main()
