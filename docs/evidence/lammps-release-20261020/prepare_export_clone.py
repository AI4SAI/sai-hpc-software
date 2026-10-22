#!/usr/bin/env python3
"""Preserve a terminal compiled release and create one independently locked clone."""
import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import finish_native_export as recovery


def output(argv):
    return subprocess.check_output(list(map(str, argv)), text=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=recovery.SOURCES, default='16V100')
    args = parser.parse_args()
    os.umask(0o077)
    sys.dont_write_bytecode = True
    run, job = recovery.SOURCES[args.target]
    old = recovery.ROOT / 'runs' / run
    new = recovery.ROOT / 'runs' / (run + '-export')
    if new.exists() or new.is_symlink() or old.resolve() != old:
        raise ValueError('export clone already exists or source path is redirected')
    request = json.loads((old / 'request.json').read_text())
    control = Path(request['controller'])
    if control.resolve() != control or not control.is_relative_to(recovery.ROOT / 'controller'):
        raise ValueError('unexpected controller path')
    sys.path.insert(0, str(control))
    from md_controller import validate_pair
    from md_tracking import fingerprint
    from source_cache import checksum
    delivery = validate_pair(request['delivery'])
    if (request['run_id'] != run or request['stage_jobs']['lammps'] != job
            or (old / 'job.id').read_text().strip() != job
            or delivery['identities']['lammps']['partition'] != args.target
            or delivery['recipe_sha256'] != recovery.RECIPE or fingerprint(control) != recovery.RECIPE
            or request['job_script_sha256'] != checksum(old / 'job.sbatch')):
        raise ValueError('compiled source identity changed')
    for stage, script in (('deepmd', 'job-deepmd.sbatch'), ('lammps', 'job.sbatch')):
        if request['stage_job_script_sha256'][stage] != checksum(old / script):
            raise ValueError('compiled stage script changed')
    if job in output(['squeue', '-h', '-u', 'stardust', '-o', '%A']).split():
        raise ValueError('source job is still active')
    accounting = output(['sacct', '-X', '-nP', '-j', job, '-o', 'JobIDRaw,State,ExitCode'])
    if accounting.strip().split('|')[:3] != [job, 'FAILED', '1:0']:
        raise ValueError('source has not reached the expected terminal failure')
    log = old / 'results' / ('slurm-' + job + '.log')
    text = log.read_text()
    if ('[100%] Built target lmp' not in text or '/control/native_module.py' not in text
            or 'ValueError: external root must identify an explicit site dependency prefix' not in text):
        raise ValueError('source is not the diagnosed post-install metadata failure')
    fixed = recovery.DIAGNOSTICS / 'preflight-native-r4/controller'
    sys.path.append(str(fixed))
    from cp2k_overlay_workdir import lock, fd_checksum
    source_fd = os.open(old / 'work.ext3', os.O_RDWR | os.O_NOFOLLOW)
    target_fd = None
    try:
        lock(source_fd, True)
        info = os.fstat(source_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('source is not an unaliased regular image')
        new.mkdir(mode=0o700)
        for name in ('input', 'results', 'runtime', 'apptainer-cache'):
            (new / name).mkdir(mode=0o700)
        os.fchmod(source_fd, 0o400)
        digest = fd_checksum(source_fd)
        target_fd = os.open(new / 'work.ext3', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        lock(target_fd, True)
        subprocess.run(['cp', '--sparse=always', '--reflink=auto', '--',
                        '/proc/self/fd/' + str(source_fd), '/proc/self/fd/' + str(target_fd)],
                       check=True, pass_fds=(source_fd, target_fd))
        os.fsync(target_fd)
        if fd_checksum(target_fd) != digest or fd_checksum(source_fd) != digest:
            raise ValueError('source or clone changed during copy')
        record = dict(source=str(old), source_job=job, overlay_sha256=digest,
                      request_sha256=checksum(old / 'request.json'), source_log_sha256=checksum(log),
                      compiled_recipe_sha256=recovery.RECIPE, original_mode=stat.S_IMODE(info.st_mode),
                      clone_tool_sha256=checksum(Path(__file__)),
                      recovery_tool_sha256=checksum(Path(recovery.__file__)),
                      lock_helper_sha256=checksum(fixed / 'cp2k_overlay_workdir.py'),
                      accounting=accounting, source_unchanged=True)
        with (new / 'input/source.json').open('x') as stream:
            json.dump(record, stream, sort_keys=True)
            stream.write('\n')
        (new / 'input/source.json').chmod(0o444)
        shutil.copyfile(old / 'input/pair.json', new / 'input/pair.json')
        print(json.dumps(record, sort_keys=True), flush=True)
    finally:
        if target_fd is not None:
            os.close(target_fd)
        os.close(source_fd)


if __name__ == '__main__':
    main()
