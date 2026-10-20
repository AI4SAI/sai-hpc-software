#!/usr/bin/env python3
"""Retry the diagnosed DSPRHBM pre-metadata mount timeout on its prepared clone."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from submit import ROOT, RECIPE, execute, isolate_script, name


def main():
    build = ROOT / 'runs' / (name('dsprhbm') + '-export')
    request = json.loads((build / 'request.json').read_text())
    job = (build / 'job.id').read_text().strip()
    if job != '1867798' or job in execute(['squeue', '-hu', 'stardust', '-o', '%A']).split():
        raise ValueError('not the diagnosed terminal attempt')
    accounting = execute(['sacct', '-X', '-nP', '-j', job, '-o', 'JobIDRaw,State,ExitCode'])
    if accounting.strip().split('|')[:3] != [job, 'FAILED', '255:0']:
        raise ValueError('unexpected failure accounting: ' + accounting)
    log = (build / 'results' / ('slurm-' + job + '.log')).read_text()
    if ('fuse2fs failed to mount ' not in log or ' in 10s' not in log
            or log.count('CP2K_IMAGE_DRIVER sha256=') != 1):
        raise ValueError('failure is not the pre-metadata image mount timeout')
    preparation = json.loads((build / 'results/overlay-workdir-preparation.json').read_text())
    if preparation['status'] != 'prepared' or not preparation['source_unchanged']:
        raise ValueError('clone preparation did not finish')
    if (build / 'artifact.path').exists() or (build / 'result.sif').exists():
        raise ValueError('artifact already exists')
    control = Path(request['controller'])
    sys.path.insert(0, str(control))
    import software_controller as frozen
    from cp2k_overlay_workdir import lock
    from source_cache import checksum
    if frozen.recipe_fingerprint('cp2k') != RECIPE:
        raise ValueError('frozen recipe changed')
    overlay = build / 'work.ext3'
    history = build / 'recovery-attempts' / job
    history.mkdir(parents=True)
    for filename in ('request.json', 'job.id', 'job.sbatch'):
        shutil.copyfile(build / filename, history / filename)
    shutil.copyfile(build / 'results/status.json', history / 'status.json')
    # Exclusive OFD locking also rejects a surviving image driver. Repair only
    # the disposable clone; keep the sealed compiler image untouched.
    fd = os.open(overlay, os.O_RDWR | os.O_NOFOLLOW)
    try:
        lock(fd, True)
        for flag, label, accepted in [('-pf', 'pre-retry-fsck', (0, 1)),
                                     ('-fn', 'post-repair-fsck', (0,))]:
            checked = subprocess.run(['/usr/sbin/e2fsck', flag, '/proc/self/fd/' + str(fd)],
                                     pass_fds=(fd,), capture_output=True, text=True)
            (history / (label + '.log')).write_text(checked.stdout + checked.stderr)
            if checked.returncode not in accepted:
                raise ValueError('clone requires manual filesystem repair')
    finally:
        os.close(fd)
    script = frozen.render_job(argparse.Namespace(**request))
    lines = script.splitlines()
    lines[lines.index('test ! -e ' + shlex.quote(str(overlay)))] = 'test -s ' + shlex.quote(str(overlay))
    index = next(i for i, line in enumerate(lines) if line.startswith('cp --sparse=always '))
    if 'sha256sum --check --status' not in lines[index + 1] or not lines[index + 2].startswith('chmod 600 '):
        raise ValueError('unexpected clone sequence')
    lines[index:index + 3] = [shlex.join(['/usr/sbin/e2fsck', '-fn', str(overlay)])]
    script = isolate_script('\n'.join(lines) + '\n')
    if 'cp2k_container_entry.sh build ' in script:
        raise ValueError('must not recompile')
    path = build / 'finish.sbatch'
    with path.open('x') as stream:
        stream.write(script)
    path.chmod(0o500)
    execute(['bash', '-n', path])
    execute(['sbatch', '--test-only', path])
    new_job = execute(['sbatch', '--hold', '--parsable', path]).strip().split(';')[0]
    if not new_job.isdigit():
        raise ValueError('ambiguous held submission')
    retry = dict(previous_job=job, job=new_job, script=str(path), script_sha256=checksum(path),
                 reason='pre-metadata fuse2fs mount timeout; prepared clone retained',
                 tool_sha256=checksum(Path(__file__)), controller=str(control), recipe=RECIPE)
    (build / 'retry-mount-timeout.json').write_text(json.dumps(retry, indent=2) + '\n')
    request.update(recovery_job=new_job, recovery_retry=retry)
    (build / 'request.json').write_text(json.dumps(request, sort_keys=True) + '\n')
    (build / 'job.id').write_text(new_job + '\n')
    execute(['scontrol', 'release', new_job])
    print(json.dumps(retry), flush=True)


if __name__ == '__main__':
    main()
