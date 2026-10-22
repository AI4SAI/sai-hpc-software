#!/usr/bin/env python3
"""Compare supported options and an upstream fix on private home-backed images."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

base = Path(sys.argv[1]).resolve()
assert str(base).startswith('/home/stardust/sai-hpc-software/diagnostics/')
apptainer = '/opt/devtools/apptainer/1.4.4/bin/apptainer'
stock = '/opt/devtools/apptainer/1.4.4/libexec/apptainer/bin/fuse2fs'
candidate = str(base / 'driver/fuse2fs-45651efd')
image = '/home/stardust/sai-hpc-software/containers/base/minimal-v1.sif'
config_text = Path('/opt/devtools/apptainer/1.4.4/etc/apptainer/apptainer.conf').read_text()


def run(argv, log, env, timeout=240):
    start = time.monotonic()
    with log.open('wb') as f:
        result = subprocess.run(argv, stdout=f, stderr=subprocess.STDOUT, env=env, timeout=timeout)
    return {'returncode': result.returncode, 'wall_seconds': time.monotonic() - start}


for label, driver, opts in [('stock-1', stock, ''), ('upstream-1', candidate, ''),
                            ('upstream-2', candidate, ''), ('stock-2', stock, '')]:
    case = base / label
    case.mkdir(mode=0o700)
    for name in ('runtime', 'cache', 'bin', 'results'):
        (case / name).mkdir(mode=0o700)
    wrapper = case / 'bin/fuse2fs'
    cmd = ['/usr/bin/strace', '-f', '-qq', '-ttt', '-T', '-e', 'trace=fsync,fdatasync',
           '-o', str(case / 'driver-fsync.trace'), driver]
    if opts:
        cmd += ['-o', opts]
    wrapper.write_text('#!/bin/sh\nexec ' + shlex.join(cmd) + ' "$@"\n')
    wrapper.chmod(0o700)
    cfg = case / 'apptainer.conf'
    cfg.write_text(config_text + '\nbinary path = ' + str(case / 'bin') + ':$PATH:/usr/bin:/bin:/usr/sbin:/sbin\n')
    env = dict(os.environ, TMPDIR=str(case / 'runtime'), APPTAINER_TMPDIR=str(case / 'runtime'),
               APPTAINER_CACHEDIR=str(case / 'cache'))
    overlay = case / 'work.ext3'
    subprocess.run([apptainer, 'overlay', 'create', '--sparse', '--size', '256', '--fakeroot', str(overlay)], env=env, check=True)
    argv = [apptainer, '--debug', '--config', str(cfg), 'exec', '--fakeroot', '--cleanenv', '--containall',
            '--no-home', '--no-mount', 'bind-paths,home,cwd,tmp,hostfs', '--pwd', '/', '--net', '--network', 'none']
    for p in ('/usr', '/sbin', '/lib', '/lib64', '/opt/devtools'):
        argv += ['--bind', p + ':' + p + ':ro']
    argv += ['--bind', str(base / 'input') + ':/bench:ro', '--bind', str(case / 'results') + ':/results:rw',
             '--env', 'TMPDIR=/workspace/tmp', '--overlay', str(overlay), image,
             '/usr/bin/python3', '/bench/fuse2fs-workload.py']
    record = {'label': label, 'driver': driver, 'options': opts, 'start_epoch': time.time()}
    print(json.dumps(record), flush=True)
    try:
        record['workload'] = run(argv + ['run'], case / 'run.log', env)
        if record['workload']['returncode'] == 0:
            # Retain the workload trace; remounting gets its own trace file.
            (case / 'driver-fsync.trace').rename(case / 'workload-fsync.trace')
            record['fsck'] = run(['/usr/sbin/e2fsck', '-fn', str(overlay)], case / 'fsck.log', env)
            record['remount'] = run(argv + ['verify'], case / 'verify.log', env)
            if record['fsck']['returncode'] or record['remount']['returncode']:
                raise RuntimeError('filesystem validation failed')
        else:
            raise RuntimeError('workload failed; see run.log')
    except Exception as exc:
        record['error'] = str(exc)
    (case / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record), flush=True)
    if 'error' in record:
        # Do not continue after a failed unmount or integrity check.
        sys.exit(1)
