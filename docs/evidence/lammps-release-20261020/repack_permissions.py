#!/usr/bin/env python3
"""Repack the compiled 4V100 payload after its eight metadata modes drifted."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

PROJECT = Path('/home/stardust/sai-hpc-software')
ROOT = PROJECT / 'experimental/deepmd-lammps'
DIAG = PROJECT / 'diagnostics/lammps-release-20261020'
RUN = 'md-39403117741-1-2026-10-20-9bf6ae82814537d7-export'
PREVIOUS = '1879413'
RECIPE = '296ed3baa11b0bb5629dfb60e7bf396083367a613719dfd8d21767082fa98097'


def output(argv):
    return subprocess.check_output(list(map(str, argv)), text=True)


def main():
    os.umask(0o077)
    sys.dont_write_bytecode = True
    task = ROOT / 'runs' / RUN
    request = json.loads((task / 'request.json').read_text())
    control = Path(request['controller'])
    sys.path.insert(0, str(control))
    from md_tracking import fingerprint
    from source_cache import checksum
    if (request['run_id'] != RUN or request['stage_jobs']['lammps'] != PREVIOUS
            or (task / 'job.id').read_text().strip() != PREVIOUS
            or request['job_script_sha256'] != checksum(task / 'job.sbatch')
            or request['delivery']['recipe_sha256'] != RECIPE or fingerprint(control) != RECIPE):
        raise ValueError('unexpected recovery identity')
    if PREVIOUS in output(['squeue', '-h', '-u', 'stardust', '-o', '%A']).split():
        raise ValueError('previous recovery is still active')
    accounting = output(['sacct', '-X', '-nP', '-j', PREVIOUS, '-o', 'JobIDRaw,State,ExitCode'])
    if accounting.strip().split('|')[:3] != [PREVIOUS, 'FAILED', '1:0']:
        raise ValueError('unexpected previous accounting')
    log = (task / 'results' / ('slurm-' + PREVIOUS + '.log')).read_text()
    if ('MD_RUNTIME_PATH_AUDIT_PASSED 29' not in log or
            'installed manifest requires regular, non-symlink path and mode 0444' not in log):
        raise ValueError('not the diagnosed packaged permission failure')
    comparison = json.loads((task / 'results/packaged-mode-diff.json').read_text())
    if (comparison['extra'] or len(comparison['changes']) != 6
            or len(comparison['manifests']) != 2
            or any(row['mode'] != '0o644' for row in comparison['manifests'])
            or any(row['expected']['mode'] != 0o444 or row['actual']['mode'] != 0o644
                   or {k: v for k, v in row['expected'].items() if k != 'mode'} !=
                      {k: v for k, v in row['actual'].items() if k != 'mode'}
                   for row in comparison['changes'])):
        raise ValueError('payload differs beyond the diagnosed metadata modes')
    history = task / 'recovery-attempts' / PREVIOUS
    history.mkdir(parents=True)
    for name in ('request.json', 'job.id', 'job.sbatch'):
        shutil.copyfile(task / name, history / name)
    for name in ('status.json', 'slurm-' + PREVIOUS + '.log', 'packaged-mode-diff.json'):
        shutil.copyfile(task / 'results' / name, history / name)
    failed = task / 'candidate.sif'
    failed.chmod(0o400)
    record = dict(previous_job=PREVIOUS, failed_image=str(failed), failed_image_sha256=checksum(failed),
                  compiled_recipe_sha256=RECIPE, payload_bytes_unchanged=True,
                  comparison_sha256=checksum(history / 'packaged-mode-diff.json'),
                  recovery_tool_sha256=checksum(Path(__file__)), accounting=accounting)
    helper = task / 'input/md_packaging.py'
    shutil.copyfile(DIAG / 'preflight-packaging-r5/controller/md_packaging.py', helper)
    helper.chmod(0o444)
    entry = task / 'input/repack-modes.sh'
    entry.write_text('''#!/usr/bin/env bash
set -euo pipefail
export SAI_MD_DELIVERY=$1 PYTHONPATH=/control TMPDIR=/workspace/tmp
env -u LD_PRELOAD /usr/bin/python3 /recovery/md_packaging.py /workspace/export
env -u LD_PRELOAD mksquashfs /workspace/export /workspace/final-modes.squashfs -noappend -all-root -no-xattrs -processors "$BUILD_JOBS"
echo MD_REPACKED_WITH_REAL_MODES_NO_RECOMPILATION
''')
    entry.chmod(0o444)
    output(['bash', '-n', entry])
    record.update(packaging_helper_sha256=checksum(helper), packaging_entry_sha256=checksum(entry))
    fixed = DIAG / 'preflight-native-r4/controller'
    sys.path.append(str(fixed))
    from cp2k_overlay_workdir import Inspector, lock
    fd = os.open(task / 'work.ext3', os.O_RDWR | os.O_NOFOLLOW)
    try:
        lock(fd, True)
        record['overlay_before_fsck_sha256'] = checksum(task / 'work.ext3')
        fsck = history / 'overlay-preparation'
        fsck.mkdir()
        fsck_record = dict(commands=[])
        inspector = Inspector(fd, fsck, fsck_record)
        inspector.command(['/usr/sbin/e2fsck', '-pf', '/proc/self/fd/' + str(fd)], allowed=(0, 1))
        upper, entries, commands = inspector.inventory()
        if commands:
            inspector.debugfs(commands, writable=True)
            os.fsync(fd)
        upper_after, remaining, unused = inspector.inventory()
        checked = inspector.command(['/usr/sbin/e2fsck', '-fn', '/proc/self/fd/' + str(fd)])
        if (upper_after != upper or remaining or unused or any(word in checked.stdout + checked.stderr
                for word in ('Fix?', ' wrong', 'WARNING', 'UNEXPECTED'))):
            raise ValueError('export workspace is not clean')
        fsck_record.update(removed_work_entries=entries, overlay_after_sha256=checksum(task / 'work.ext3'))
        (history / 'overlay-preparation.json').write_text(json.dumps(fsck_record, sort_keys=True) + '\n')
    finally:
        os.close(fd)
    lines = (history / 'job.sbatch').read_text().splitlines()
    script = []
    for line in lines:
        if '/control/md_container_entry.sh export ' in line:
            continue
        line = line.replace('/recovery/finish-metadata.sh', '/recovery/repack-modes.sh')
        line = line.replace('/workspace/final.squashfs', '/workspace/final-modes.squashfs')
        line = line.replace(str(task / 'final.squashfs'), str(task / 'final-modes.squashfs'))
        line = line.replace(str(task / 'candidate.sif'), str(task / 'candidate-modes.sif'))
        line = line.replace('#SBATCH --qos=flood-1o2gpu', '#SBATCH --qos=rush-1o2gpu')
        script.append(line)
    path = task / 'job.sbatch'
    path.write_text('\n'.join(script) + '\n')
    if 'build-lammps' in path.read_text() or 'finish-metadata.sh' in path.read_text():
        raise ValueError('unexpected rebuilding command')
    output(['bash', '-n', path])
    output(['sbatch', '--test-only', path])
    job = output(['sbatch', '--hold', '--parsable', path]).strip().split(';')[0]
    if not job.isdigit():
        raise ValueError('ambiguous held submission')
    record.update(job=job, job_script_sha256=checksum(path))
    request['metadata_recovery']['packaging_recovery'] = record
    request['stage_jobs']['lammps'] = job
    request['stage_job_script_sha256']['lammps'] = request['job_script_sha256'] = checksum(path)
    (task / 'request.json').write_text(json.dumps(request, sort_keys=True) + '\n')
    (task / 'job.id').write_text(job + '\n')
    (task / 'input/packaging-recovery.json').write_text(json.dumps(record, sort_keys=True) + '\n')
    output(['scontrol', 'release', job])
    print(json.dumps(record, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
