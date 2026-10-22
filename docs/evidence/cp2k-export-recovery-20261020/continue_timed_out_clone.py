#!/usr/bin/env python3
"""Finish 8V100V0's interrupted final clone checks on the login node, then export."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys

from submit import ROOT, RECIPE, execute, isolate_script, name


def main():
    build = ROOT / 'runs' / (name('8v100v0-avx512') + '-export')
    request = json.loads((build / 'request.json').read_text())
    job = (build / 'job.id').read_text().strip()
    if job != '1867805' or job in execute(['squeue', '-hu', 'stardust', '-o', '%A']).split():
        raise ValueError('not the diagnosed terminal attempt')
    accounting = execute(['sacct', '-X', '-nP', '-j', job, '-o', 'JobIDRaw,State,ExitCode'])
    if accounting.strip().split('|')[:3] != [job, 'TIMEOUT', '0:0']:
        raise ValueError('unexpected failure accounting: ' + accounting)
    old = build / 'results/overlay-workdir-preparation'
    log = (build / 'results' / ('slurm-' + job + '.log')).read_text()
    if ('DUE TO TIME LIMIT' not in log or 'CP2K_IMAGE_DRIVER' in log
            or not (old / '009.stdout').exists()
            or (old / '002.stdout').read_bytes() != (old / '007.stdout').read_bytes()):
        raise ValueError('not the diagnosed post-cleanup, post-fsck timeout')
    if (build / 'artifact.path').exists() or (build / 'result.sif').exists():
        raise ValueError('artifact already exists')
    control = Path(request['controller'])
    sys.path.insert(0, str(control))
    import software_controller as frozen
    from cp2k_overlay_workdir import Inspector, fd_checksum, lock
    from source_cache import checksum
    if frozen.recipe_fingerprint('cp2k') != RECIPE:
        raise ValueError('frozen recipe changed')
    source = ROOT / 'runs' / request['resume_run'] / 'work.ext3'
    overlay = build / 'work.ext3'
    history = build / 'recovery-attempts' / job
    history.mkdir(parents=True)
    for filename in ('request.json', 'job.id', 'job.sbatch'):
        shutil.copyfile(build / filename, history / filename)
    shutil.copyfile(build / 'results/status.json', history / 'status.json')
    results = build / 'results/overlay-workdir-continuation'
    results.mkdir()
    report = dict(schema=1, scope='/work/work', commands=[], source=str(source), target=str(overlay),
                  previous_job=job, interrupted_after='cleanup and second read-only fsck; final checksums incomplete',
                  previous_logs={p.name: checksum(p) for p in old.iterdir()},
                  status='checking')
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    target_fd = os.open(overlay, os.O_RDWR | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(source_fd).st_mode) or os.fstat(source_fd).st_mode & 0o222:
            raise ValueError('compiler image is not sealed')
        lock(source_fd, False)
        lock(target_fd, True)
        observed = fd_checksum(source_fd)
        if observed != request['resume_overlay_sha256']:
            raise ValueError('sealed compiler image changed')
        report.update(source_overlay_sha256=observed, source_unchanged=True)
        inspector = Inspector(target_fd, results, report)
        upper_before, entries, commands = inspector.inventory()
        if entries or commands:
            raise ValueError('interrupted clone cleanup was not complete')
        # The original helper verified the copied checksum before these nine
        # captured commands. Only filesystem counters need repair afterward.
        inspector.command(['/usr/sbin/e2fsck', '-pf', '/proc/self/fd/' + str(target_fd)], allowed=(0, 1))
        inspector.command(['/usr/sbin/e2fsck', '-fn', '/proc/self/fd/' + str(target_fd)])
        upper_after, entries, commands = inspector.inventory()
        if upper_after != upper_before or entries or commands:
            raise ValueError('unexpected filesystem change')
        report.update(prepared_overlay_sha256=fd_checksum(target_fd),
                      upper_directory_unchanged=True, status='prepared')
    finally:
        os.close(target_fd)
        os.close(source_fd)
        (build / 'results/overlay-workdir-continuation.json').write_text(json.dumps(report, indent=2) + '\n')
    lines = frozen.render_job(argparse.Namespace(**request)).splitlines()
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
                 reason='finish interrupted clone checks; retain copied compiled files',
                 tool_sha256=checksum(Path(__file__)), controller=str(control), recipe=RECIPE)
    (build / 'retry-clone-timeout.json').write_text(json.dumps(retry, indent=2) + '\n')
    request.update(recovery_job=new_job, recovery_retry=retry)
    (build / 'request.json').write_text(json.dumps(request, sort_keys=True) + '\n')
    (build / 'job.id').write_text(new_job + '\n')
    execute(['scontrol', 'release', new_job])
    print(json.dumps(retry), flush=True)


if __name__ == '__main__':
    main()
