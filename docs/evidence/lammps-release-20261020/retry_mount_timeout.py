#!/usr/bin/env python3
"""Retry a scoped pre-entry mount failure using its unchanged compiled recipe."""
import argparse
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path('/home/stardust/sai-hpc-software/experimental/deepmd-lammps')
RUN = 'md-39403084059-1-2026-10-20-5fef92e054bacdaf'
RECIPE = '296ed3baa11b0bb5629dfb60e7bf396083367a613719dfd8d21767082fa98097'
COMMIT = '9d1985ea5ba0030a5df162a93bff2f4496449d4a'
PREVIOUS = '1877775'
DEEPMD = '1877774'


def execute(argv):
    return subprocess.check_output(list(map(str, argv)), text=True)


class FileLock(ctypes.Structure):
    _fields_ = [('type', ctypes.c_short), ('whence', ctypes.c_short),
               ('start', ctypes.c_longlong), ('length', ctypes.c_longlong),
               ('pid', ctypes.c_int)]


def main():
    build = ROOT / 'runs' / RUN
    control = ROOT / 'controller' / COMMIT / RUN
    sys.path.insert(0, str(control))
    from md_controller import validate_pair, artifact_path
    from md_tracking import fingerprint
    from source_cache import checksum
    request = json.loads((build / 'request.json').read_text())
    delivery = validate_pair(request['delivery'])
    if (request['run_id'] != RUN or request['controller'] != str(control)
            or request['stage_jobs'] != {'deepmd': DEEPMD, 'lammps': PREVIOUS}
            or (build / 'job.id').read_text().strip() != PREVIOUS
            or delivery['recipe_sha256'] != RECIPE or fingerprint(control) != RECIPE):
        raise ValueError('retry does not match the diagnosed immutable run')
    for stage, filename in (('deepmd', 'job-deepmd.sbatch'), ('lammps', 'job.sbatch')):
        if checksum(build / filename) != request['stage_job_script_sha256'][stage]:
            raise ValueError('stage script changed')
    if checksum(build / 'job.sbatch') != request['job_script_sha256']:
        raise ValueError('final stage script changed')
    if {PREVIOUS, DEEPMD} & set(execute(['squeue', '-h', '-u', 'stardust', '-o', '%A']).split()):
        raise ValueError('previous jobs must be terminal')
    accounting = execute(['sacct', '-X', '-nP', '-j', PREVIOUS + ',' + DEEPMD,
                          '-o', 'JobIDRaw,State,ExitCode'])
    states = {row[0]: row[1:3] for line in accounting.splitlines() if (row := line.split('|'))[0]}
    if states != {PREVIOUS: ['FAILED', '255:0'], DEEPMD: ['COMPLETED', '0:0']}:
        raise ValueError('unexpected accounting: ' + accounting)
    log = (build / 'results' / ('slurm-' + PREVIOUS + '.log')).read_text()
    if ('fuse2fs failed to mount ' not in log or ' in 10s' not in log
            or log.count('CP2K_IMAGE_DRIVER sha256=') != 1
            or any(marker in log for marker in ('MD_NATIVE_DRIVER_PATHS', 'MD_UPSTREAM_RELEASE_TAG',
                                               'MD_COMPILER', 'MD_DEEPMD_REGISTRATION', 'Building '))):
        raise ValueError('failure is not the pre-entry mount timeout')
    prior_status = json.loads((build / 'results/status.json').read_text())
    if (prior_status.get('job') != PREVIOUS or prior_status.get('build_verified') is not False
            or prior_status.get('state') != 'FAILED' or prior_status.get('exit_code') != '255:0'):
        raise ValueError('unexpected prior status')
    for path in (build / 'artifact.path', build / 'candidate.sif', build / 'final.squashfs',
                 artifact_path(ROOT, delivery, RUN)):
        if path.exists() or path.is_symlink():
            raise ValueError('a candidate or export already exists')
    history = build / 'recovery-attempts' / PREVIOUS
    history.mkdir(parents=True, exist_ok=True)
    for name in ('request.json', 'job.id', 'job.sbatch', 'job-deepmd.sbatch'):
        if (history / name).exists():
            if (history / name).read_bytes() != (build / name).read_bytes():
                raise ValueError('prior retry preparation differs from current run')
        else:
            shutil.copyfile(build / name, history / name)
    for name in ('status.json', 'deepmd-status.json', 'slurm-' + PREVIOUS + '.log'):
        if (history / name).exists():
            if (history / name).read_bytes() != (build / 'results' / name).read_bytes():
                raise ValueError('prior retry evidence differs from current run')
        else:
            shutil.copyfile(build / 'results' / name, history / name)
    (history / 'accounting.txt').write_text(accounting)
    fd = os.open(build / 'work.ext3', os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.fcntl(fd, fcntl.F_OFD_SETLK, bytes(FileLock(fcntl.F_WRLCK, os.SEEK_SET, 0, 0, 0)))
        digest = hashlib.sha256()
        offset = 0
        while data := os.pread(fd, 8 * 1024 * 1024, offset):
            digest.update(data)
            offset += len(data)
        checked = subprocess.run(['/usr/sbin/e2fsck', '-fn', '/proc/self/fd/' + str(fd)],
                                 pass_fds=(fd,), capture_output=True, text=True, timeout=180)
        fsck_name = 'pre-retry-fsck-recheck.log' if (history / 'pre-retry-fsck.log').exists() else 'pre-retry-fsck.log'
        (history / fsck_name).write_text(checked.stdout + checked.stderr)
        if checked.returncode != 0:
            raise ValueError('overlay failed read-only inspection; no repair or submission performed')
        before_digest = digest.hexdigest()
        # Apptainer terminated the prior driver during unmount. Its read-only
        # check found only stale free-space counters, despite exiting zero.
        # Keep the exact old image before preening the resumable workspace.
        archive = history / 'work.ext3.before-fsck'
        prepared_before = archive.exists()
        if prepared_before:
            if (archive.is_symlink() or archive.stat().st_mode & 0o222
                    or not (history / 'post-repair-fsck.log').is_file()
                    or any(word in (history / 'post-repair-fsck.log').read_text()
                           for word in ('Fix?', ' wrong', 'WARNING', 'UNEXPECTED'))):
                raise ValueError('existing preparation is incomplete or inconsistent')
            before_digest = checksum(archive)
        else:
            execute(['cp', '--sparse=always', '--reflink=auto', '--', build / 'work.ext3', archive])
            if checksum(archive) != before_digest:
                raise ValueError('preserved image differs from the locked original')
            archive.chmod(0o400)
            repaired = subprocess.run(['/usr/sbin/e2fsck', '-pf', '/proc/self/fd/' + str(fd)],
                                      pass_fds=(fd,), capture_output=True, text=True, timeout=180)
            (history / 'repair-fsck.log').write_text(repaired.stdout + repaired.stderr)
            if repaired.returncode not in (0, 1):
                raise ValueError('automatic filesystem check failed; original image is preserved')
        checked = subprocess.run(['/usr/sbin/e2fsck', '-fn', '/proc/self/fd/' + str(fd)],
                                 pass_fds=(fd,), capture_output=True, text=True, timeout=180)
        fsck_name = 'post-repair-fsck-recheck.log' if prepared_before else 'post-repair-fsck.log'
        (history / fsck_name).write_text(checked.stdout + checked.stderr)
        if checked.returncode != 0 or any(word in checked.stdout + checked.stderr
                                         for word in ('Fix?', ' wrong', 'WARNING', 'UNEXPECTED')):
            raise ValueError('filesystem still needs repair; original image is preserved')
        execute(['bash', '-n', build / 'job.sbatch'])
        # The completed predecessor has expired from scontrol's active job
        # table. Its accounting was checked above; override only the stale
        # afterok dependency while retaining singleton and the script bytes.
        execute(['sbatch', '--test-only', '--dependency=singleton', build / 'job.sbatch'])
        job = execute(['sbatch', '--hold', '--parsable', '--dependency=singleton',
                       build / 'job.sbatch']).strip().split(';')[0]
        if not job.isdigit():
            raise ValueError('ambiguous held submission')
        retry = dict(previous_job=PREVIOUS, job=job, completed_deepmd_job=DEEPMD,
                     reason='pre-entry fuse2fs mount timeout; unchanged LAMMPS stage retried once',
                     controller=str(control), recipe_sha256=RECIPE,
                     script_sha256=checksum(build / 'job.sbatch'),
                     original_overlay_sha256=before_digest, original_overlay=str(archive),
                     overlay_sha256_before_retry=checksum(build / 'work.ext3'),
                     fsck='original archived; e2fsck -pf then clean read-only -fn',
                     dependency_override='singleton; completed predecessor verified in sacct',
                     tool_sha256=checksum(Path(__file__)),
                     previous_attempt=str(history))
        with (build / 'retry-mount-timeout.json').open('x') as stream:
            json.dump(retry, stream, indent=2)
            stream.write('\n')
        request['stage_jobs']['lammps'] = job
        request['mount_timeout_retry'] = retry
        (build / 'request.json').write_text(json.dumps(request, sort_keys=True) + '\n')
        (build / 'job.id').write_text(job + '\n')
    finally:
        os.close(fd)
    execute(['scontrol', 'release', job])
    print(json.dumps(retry, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=('16V100', '8V100V0'), default='16V100')
    args = parser.parse_args()
    if args.target == '8V100V0':
        RUN = 'md-39427644750-1-2026-10-20-1dfd6f53c113abda'
        RECIPE = '795dbd80e2a43d3e7a32c1bc373d30b88476fcca8d63f26ccc6d42a84515707c'
        COMMIT = '0ab58dd71054a26cf9e666e31991971757eedfe5'
        PREVIOUS = '1881546'
        DEEPMD = '1881545'
    main()
