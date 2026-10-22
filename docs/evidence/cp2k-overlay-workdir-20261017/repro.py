#!/usr/bin/env python3
"""Isolated two-case probe; creates only new 256 MiB diagnostic images."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time

PROJECT = Path('/home/stardust/sai-hpc-software')
BASE = PROJECT / 'diagnostics/cp2k-release-20261016/overlay-workdir-repro-20261017-b'
APPTAINER = Path('/opt/devtools/apptainer/1.4.4/bin/apptainer')
CONFIG = Path('/opt/devtools/apptainer/1.4.4/etc/apptainer/apptainer.conf')
DRIVER_SHA = '0680f553de17cba684a6f5ea265c9d80bd6d576d68aebc4e788e2609a66ad5e9'
DRIVER = PROJECT / ('cache/tools/fuse2fs-' + DRIVER_SHA + '/bin/fuse2fs')
OVERLAY_DRIVER = Path('/opt/devtools/apptainer/1.4.4/libexec/apptainer/bin/fuse-overlayfs')


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            result.update(data)
    return result.hexdigest()


def workload():
    import errno
    root = Path('/workspace/rm-probe')
    if not root.is_dir():
        raise RuntimeError('precreated overlay probe directory is missing')
    failures = []
    for i in range(8192):
        path = root / ('file-%05d' % i)
        try:
            # Keep the descriptor until after unlink, like generated/open build files.
            with path.open('w') as stream:
                stream.write('test\n')
                stream.flush()
                path.unlink()
        except OSError as error:
            failures.append(dict(iteration=i, errno=error.errno, error=str(error)))
            if len(failures) >= 16:
                break
        if i % 1024 == 0:
            print('iteration', i, flush=True)
    result = dict(failures=failures, last_iteration=i,
                  eisdir_count=sum(row['errno'] == errno.EISDIR for row in failures))
    Path('/results/workload.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)


def run_case(label, count, environment):
    case = BASE / label
    case.mkdir()
    for directory in ('bin', 'results'):
        (case / directory).mkdir(parents=True, exist_ok=True)
    image = case / 'fresh.ext3'
    with (case / 'mkfs.log').open('w') as log:
        subprocess.run([str(APPTAINER), 'overlay', 'create', '--sparse', '--size', '256',
                        '--fakeroot', str(image)],
                       env=environment, check=True, stdout=log, stderr=subprocess.STDOUT)
    empty = case / 'empty'
    empty.touch()
    commands = ['mkdir /upper/workspace', 'mkdir /upper/workspace/rm-probe',
                'mkdir /work/work']
    for i in range(1, count + 1):
        commands += ['mkdir /work/work/' + str(i),
                     'write ' + str(empty) + ' /work/work/' + str(i) + '/.wh..wh..opq']
    seed = case / 'seed.debugfs'
    seed.write_text('\n'.join(commands) + '\n')
    with (case / 'seed.log').open('w') as log:
        subprocess.run(['/usr/sbin/debugfs', '-w', '-f', str(seed), str(image)],
                       env=environment, check=True, stdout=log, stderr=subprocess.STDOUT)
    for name, binary in [('fuse2fs', DRIVER), ('fuse-overlayfs', OVERLAY_DRIVER)]:
        wrapper = case / 'bin' / name
        traced = ['/usr/bin/strace', '-f', '-qq', '-tt', '-s', '256',
                  '-e', 'trace=renameat,renameat2,unlinkat,getdents64',
                  '-o', str(case / (name + '.trace')), str(binary)]
        wrapper.write_text('#!/bin/sh\nexec ' + shlex.join(traced) + ' "$@"\n')
        wrapper.chmod(0o700)
    config = case / 'apptainer.conf'
    config.write_text(CONFIG.read_text() + '\nbinary path = ' + str(case / 'bin') +
                      ':$PATH:/usr/bin:/bin:/usr/sbin:/sbin\n')
    argv = [str(APPTAINER), '--debug', '--config', str(config), 'exec', '--fakeroot',
            '--cleanenv', '--containall', '--no-home', '--no-mount',
            'bind-paths,home,cwd,tmp,hostfs', '--pwd', '/', '--net', '--network', 'none']
    for path in ('/usr', '/sbin', '/lib', '/lib64', '/opt/devtools'):
        argv += ['--bind', path + ':' + path + ':ro']
    argv += ['--bind', str(BASE / 'input') + ':/probe:ro',
             '--bind', str(case / 'results') + ':/results:rw',
             '--env', 'PYTHONDONTWRITEBYTECODE=1,TMPDIR=/workspace',
             '--overlay', str(image), str(PROJECT / 'containers/base/minimal-v1.sif'),
             '/usr/bin/python3', '-B', '/probe/repro.py', '--workload']
    started = time.monotonic()
    with (case / 'container.log').open('w') as log:
        completed = subprocess.run(argv, env=environment, stdout=log,
                                   stderr=subprocess.STDOUT, timeout=480)
    listing = subprocess.run(['/usr/sbin/debugfs', '-R', 'ls -l /work/work', str(image)],
                             capture_output=True, text=True, check=True)
    (case / 'workdir-after.txt').write_text(listing.stdout + listing.stderr)
    trace_path = case / 'fuse-overlayfs.trace'
    trace = trace_path.read_text(errors='replace') if trace_path.exists() else ''
    result = dict(label=label, initial_numeric_directories=count,
                  returncode=completed.returncode, wall_seconds=time.monotonic() - started,
                  image=str(image), image_sha256=digest(image), argv=argv,
                  eisdir_trace_lines=[line for line in trace.splitlines() if 'EISDIR' in line],
                  rename_eisdir_lines=[line for line in trace.splitlines()
                                      if 'renameat' in line and 'EISDIR' in line])
    output = case / 'results/workload.json'
    if output.exists():
        result['workload'] = json.loads(output.read_text())
    (case / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('label', 'returncode', 'wall_seconds')}), flush=True)
    return result


def main():
    if sys.argv[1:] == ['--workload']:
        workload()
        return
    if Path(__file__).resolve() != BASE / 'input/repro.py':
        raise RuntimeError('deploy only inside the fixed home diagnostic directory')
    if digest(DRIVER) != DRIVER_SHA:
        raise RuntimeError('pinned writable driver changed')
    for directory in ('runtime', 'cache'):
        (BASE / directory).mkdir(exist_ok=True)
    env = dict(os.environ, TMPDIR=str(BASE / 'runtime'),
               APPTAINER_TMPDIR=str(BASE / 'runtime'),
               APPTAINER_CACHEDIR=str(BASE / 'cache'), PYTHONDONTWRITEBYTECODE='1')
    records = [run_case('clean', 0, env), run_case('leftovers', 4096, env)]
    result = dict(schema=1, job_id=os.environ.get('SLURM_JOB_ID'), node=socket.gethostname(),
                  driver=str(DRIVER), driver_sha256=DRIVER_SHA,
                  cases=records, historical_images_modified=False)
    (BASE / 'result.json').write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
