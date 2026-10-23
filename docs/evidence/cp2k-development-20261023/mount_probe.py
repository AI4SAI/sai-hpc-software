#!/usr/bin/env python3
"""Two read-only attempts after CP2K job 1914241's actual mount timeout."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path('/org/org-open-sai/proj-sai-hpc-software')
BUILD = '39983165813-1-development-4v100-avx512-2026-10-23-cfe5e5d87aaf'
CONTROL = ROOT / 'controller/10992f818af71543bb39cb81e0f60c9f7ede9535' / BUILD
OVERLAY = ROOT / 'runs' / BUILD / 'work.ext3'
PREFIX = '/opt/apps/cp2k/cp2k-development-master-2026-10-23-gcfe5e5d87aaf-rae6467ac2934/4V100'


def overlay_stat():
    value = OVERLAY.stat()
    return dict(inode=value.st_ino, device=value.st_dev, size=value.st_size,
                blocks=value.st_blocks, mtime_ns=value.st_mtime_ns,
                mode=value.st_mode, uid=value.st_uid, gid=value.st_gid)


def helper_samples():
    result = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid() or (entry / 'comm').read_text().strip() != 'fuse2fs':
                continue
            command = (entry / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
            if 'fuse2fs-46c1a8c8f42ab2419ea88072e41d89903c453d93f52485b5baddea5ca238bb10' not in command:
                continue
            descriptors = []
            for fd in (entry / 'fd').iterdir():
                try:
                    name = str(fd.readlink())
                    if name == str(OVERLAY):
                        descriptors.append(dict(path=name, fdinfo=(entry / 'fdinfo' / fd.name).read_text()))
                except (FileNotFoundError, PermissionError):
                    pass
            result.append(dict(pid=int(entry.name), command=command, backing_descriptors=descriptors,
                               wchan=(entry / 'wchan').read_text(), io=(entry / 'io').read_text()))
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return result


def main():
    task = Path(sys.argv[1])
    if task.resolve() != task or task.parent != ROOT / 'runs' or os.environ.get('SLURM_JOB_PARTITION') != '4V100':
        raise ValueError('probe requires its project run and a 4V100 allocation')
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(CONTROL))
    import cp2k_image_driver as driver
    config = driver.prepare(ROOT, task)
    before = overlay_stat()
    argv = [str(driver.APPTAINER), '--debug', '--config', str(config), 'exec',
            '--fakeroot', '--cleanenv', '--containall', '--no-home',
            '--no-mount', 'bind-paths,home,cwd,tmp,hostfs', '--pwd', '/',
            '--net', '--network', 'none', '--nv']
    for name in ('/usr', '/sbin', '/lib', '/lib64', '/opt/devtools', '/opt/modules'):
        argv += ['--bind', name + ':' + name + ':ro']
    argv += ['--bind', str(task / 'runtime') + ':/runtime:rw',
             '--bind', str(task / 'runtime') + ':/tmp:rw', '--env', 'TMPDIR=/runtime',
             '--env', 'HOME=/runtime', '--overlay', str(OVERLAY) + ':ro',
             str(ROOT / 'containers/base/minimal-v1.sif'), '/usr/bin/bash', '-c',
             'test -d "$1/dependencies/spack" && printf "FUSE_RO_OVERLAY_PRESENT\\n"', 'bash', PREFIX]
    results = []
    for attempt in (1, 2):
        start = time.monotonic()
        observed = datetime.now(timezone.utc).isoformat()
        snapshots = []
        log = task / 'results' / ('attempt-%d.log' % attempt)
        timed_out = False
        with log.open('x') as stream:
            process = subprocess.Popen(argv, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            while process.poll() is None:
                helpers = helper_samples()
                if helpers:
                    snapshots.append(dict(elapsed=time.monotonic() - start, helpers=helpers))
                if time.monotonic() - start > 60:
                    timed_out = True
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                    break
                time.sleep(0.5)
            returncode = process.wait()
        result = dict(attempt=attempt, observed_at=observed, elapsed=time.monotonic() - start,
                      returncode=returncode, diagnostic_deadline_exceeded=timed_out,
                      helpers=snapshots, log=str(log), log_sha256=hashlib.sha256(log.read_bytes()).hexdigest())
        results.append(result)
        with (task / 'results' / ('attempt-%d.json' % attempt)).open('x') as stream:
            json.dump(result, stream, sort_keys=True, indent=2)
            stream.write('\n')
        print(json.dumps({key: value for key, value in result.items() if key != 'helpers'}), flush=True)
    after = overlay_stat()
    report = dict(purpose=__doc__, job_id=os.environ['SLURM_JOB_ID'], argv=argv,
                  controller=str(CONTROL), overlay=str(OVERLAY), overlay_stat_before=before,
                  overlay_stat_after=after, overlay_metadata_unchanged=before == after,
                  driver=json.loads((task / 'results/image-driver.json').read_text()), attempts=results,
                  content_sha256_recomputed=False, read_only_mount=True)
    with (task / 'results/probe.json').open('x') as stream:
        json.dump(report, stream, sort_keys=True, indent=2)
        stream.write('\n')
    if before != after:
        raise ValueError('read-only probe changed overlay metadata')


if __name__ == '__main__':
    main()
