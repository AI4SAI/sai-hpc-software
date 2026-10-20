#!/usr/bin/env python3
"""Extract the already completed SquashFS read-only, then run the frozen SIF verifier."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

ROOT = Path('/home/stardust/sai-hpc-software/experimental/deepmd-lammps')
BUILD = 'md-39427644750-1-2026-10-20-1dfd6f53c113abda'
PREVIOUS = '1882104'
TASK = ROOT / 'runs' / BUILD


def checksum(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write('\n')


def extract():
    request = json.loads((TASK / 'request.json').read_text())
    recovery = request['artifact_extraction_recovery']
    if recovery['helper_sha256'] != checksum(Path(__file__)):
        raise ValueError('frozen extraction helper changed')
    sys.path.insert(0, request['controller'])
    from md_science import require_execution_context
    require_execution_context()
    target = TASK / 'final.squashfs'
    if target.exists() or target.is_symlink():
        raise ValueError('extraction destination already exists')
    overlay = TASK / 'work.ext3'
    fd = os.open(overlay, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = os.fstat(fd)
        image_sha = checksum(overlay)
        commands = []
        def debugfs(command):
            argv = ['/usr/sbin/debugfs', '-R', command, '/proc/self/fd/' + str(fd)]
            result = subprocess.run(argv, pass_fds=(fd,), check=True, capture_output=True, text=True)
            commands.append(dict(argv=argv, stdout=result.stdout, stderr=result.stderr))
            return result.stdout
        stat = debugfs('stat /upper/workspace/final.squashfs')
        size = re.search(r'\bSize:\s+(\d+)', stat)
        if not size or int(size[1]) < 100_000_000:
            raise ValueError('completed SquashFS inode is missing or implausibly small')
        debugfs('dump /upper/workspace/final.squashfs ' + str(target))
        if target.stat().st_size != int(size[1]) or target.open('rb').read(4) != b'hsqs':
            raise ValueError('extracted file is not the complete SquashFS inode')
        after = os.fstat(fd)
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('read-only source image changed')
        write(TASK / 'results/artifact-extraction.json', dict(
            source_job=PREVIOUS, source_overlay=str(overlay), source_overlay_sha256=image_sha,
            source_open_mode='O_RDONLY', debugfs_writes_disabled=True, commands=commands,
            completed_squashfs=str(target), squashfs_sha256=checksum(target), squashfs_bytes=target.stat().st_size,
            compiled_payload_unchanged=True, no_recompilation=True, helper_sha256=checksum(Path(__file__))))
    finally:
        os.close(fd)


def prepare():
    accounting = subprocess.check_output(['sacct', '-X', '-nP', '-j', PREVIOUS,
                                          '-o', 'JobIDRaw,State,ExitCode'], text=True)
    if accounting.strip().split('|')[:3] != [PREVIOUS, 'FAILED', '255:0']:
        raise ValueError('not the expected terminal extraction failure')
    active = subprocess.check_output(['squeue', '-h', '-u', 'stardust', '-o', '%A'], text=True).split()
    if PREVIOUS in active:
        raise ValueError('original job is still active')
    request = json.loads((TASK / 'request.json').read_text())
    control = Path(request['controller'])
    sys.path.insert(0, str(control))
    from md_tracking import fingerprint
    from md_controller import artifact_path
    if (request['stage_jobs']['lammps'] != PREVIOUS or (TASK / 'job.id').read_text().strip() != PREVIOUS
            or request['delivery']['recipe_sha256'] != fingerprint(control)
            or request['job_script_sha256'] != checksum(TASK / 'job.sbatch')
            or artifact_path(ROOT, request['delivery'], BUILD).exists()
            or (TASK / 'candidate.sif').exists()):
        raise ValueError('unexpected build identity or already-created artifact')
    log = (TASK / 'results' / ('slurm-' + PREVIOUS + '.log')).read_text()
    for marker in ('MD_NATIVE_BUILD_FEATURE_PARITY_PASSED', 'MD_PACKAGED_MODES_VERIFIED',
                   'Exportable Squashfs 4.0 filesystem', 'fuse2fs failed to mount'):
        if marker not in log:
            raise ValueError('completed build/export or diagnosed extraction failure missing')
    if (TASK / 'final.squashfs').stat().st_size != 0:
        raise ValueError('unexpected partial host extraction; inspect instead of replacing it')
    with (TASK / 'results/follow-build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        history = TASK / 'recovery-attempts' / PREVIOUS
        history.mkdir()
        for name in ('request.json', 'job.sbatch', 'job.id'):
            shutil.copyfile(TASK / name, history / name)
        for name in ('status.json', 'slurm-' + PREVIOUS + '.log'):
            source = TASK / 'results' / name
            if source.exists():
                shutil.copyfile(source, history / name)
        (TASK / 'final.squashfs').rename(history / 'empty-final.squashfs')
        helper = TASK / 'input/recover-completed-squashfs.py'
        shutil.copyfile(__file__, helper)
        helper.chmod(0o444)
        lines, counts = [], dict(build=0, pack=0, extract=0)
        for line in (history / 'job.sbatch').read_text().splitlines():
            if line.startswith(('#SBATCH --dependency=', '#SBATCH --kill-on-invalid-dep=')):
                continue
            if 'md_container_entry.sh build-lammps ' in line:
                counts['build'] += 1
                continue
            if 'md_container_entry.sh export ' in line:
                counts['pack'] += 1
                continue
            if '/usr/bin/cat /workspace/final.squashfs > ' in line:
                counts['extract'] += 1
                line = shlex.join(['/usr/bin/python3', str(helper), 'extract'])
            line = line.replace('#SBATCH --gpus-per-node=4', '#SBATCH --gpus-per-node=1')
            line = line.replace('#SBATCH --qos=rush-gpu', '#SBATCH --qos=rush-1o2gpu')
            line = line.replace('#SBATCH --time=330', '#SBATCH --time=45')
            line = line.replace(' -ge 24 ]]', ' -ge 6 ]]')
            line = line.replace('BUILD_JOBS=24', 'BUILD_JOBS=6')
            if line.startswith('echo MD_BUILD_RESOURCES'):
                line = 'echo MD_EXTRACT_COMPLETED_SQUASHFS_NO_RECOMPILATION'
            lines.append(line)
        if counts != dict(build=1, pack=1, extract=1):
            raise ValueError('unexpected original stage layout')
        script = TASK / 'job.sbatch'
        script.write_text('\n'.join(lines) + '\n')
        subprocess.run(['bash', '-n', str(script)], check=True)
        record = dict(previous_job=PREVIOUS, previous_request_sha256=checksum(history / 'request.json'),
                      previous_script_sha256=checksum(history / 'job.sbatch'), accounting=accounting,
                      reason='Final SquashFS already complete; extract its inode without remounting overlay',
                      no_recompilation=True, compiled_recipe_sha256=request['delivery']['recipe_sha256'],
                      helper_sha256=checksum(helper), job_script_sha256=checksum(script), history=str(history))
        env = dict(PATH='/usr/bin:/bin', HOME=str(Path.home()), USER='stardust', LOGNAME='stardust', SBATCH_EXPORT='ALL')
        job = subprocess.check_output(['sbatch', '--hold', '--parsable', str(script)], env=env, text=True).strip()
        assert job.isdigit()
        record['job'] = job
        request['artifact_extraction_recovery'] = record
        request['stage_jobs']['lammps'] = job
        request['stage_job_script_sha256']['lammps'] = request['job_script_sha256'] = checksum(script)
        (TASK / 'request.json').write_text(json.dumps(request, sort_keys=True) + '\n')
        (TASK / 'job.id').write_text(job + '\n')
        write(history / 'extraction-recovery.json', record)
        subprocess.run(['scontrol', 'release', job], check=True)
        print(json.dumps(record, sort_keys=True))


if __name__ == '__main__':
    os.umask(0o077)
    sys.dont_write_bytecode = True
    if sys.argv[1:] == ['extract']:
        extract()
    elif sys.argv[1:] == ['prepare']:
        prepare()
    else:
        raise SystemExit('usage: recover_8v_squashfs.py prepare|extract')
