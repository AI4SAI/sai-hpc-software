#!/usr/bin/env python3
"""Diagnose the installed Apptainer driver on new project-backed overlays."""
from pathlib import Path
import hashlib
import json
import os
import shlex
import socket
import subprocess
import time

ROOT = Path('/org/org-open-sai/proj-sai-hpc-software')
BASE = Path(__file__).resolve().parent
APP = '/opt/devtools/apptainer/1.4.4/bin/apptainer'
SHA = '46c1a8c8f42ab2419ea88072e41d89903c453d93f52485b5baddea5ca238bb10'
DRIVER = ROOT / 'cache/tools' / ('fuse2fs-' + SHA) / 'bin/fuse2fs'
IMAGE = ROOT / 'containers/base/minimal-v1.sif'


def private(path):
    path.mkdir(exist_ok=True)
    for name in os.listxattr(path):
        if name in ('system.posix_acl_access', 'system.posix_acl_default'):
            os.removexattr(path, name)
    path.chmod(0o700)


def main():
    assert hashlib.sha256(DRIVER.read_bytes()).hexdigest() == SHA
    for name in ('runtime', 'cache', 'cases'):
        private(BASE / name)
    env = dict(os.environ, TMPDIR=str(BASE / 'runtime'),
               APPTAINER_TMPDIR=str(BASE / 'runtime'),
               APPTAINER_CACHEDIR=str(BASE / 'cache'), PYTHONDONTWRITEBYTECODE='1')
    site = Path('/opt/devtools/apptainer/1.4.4/etc/apptainer/apptainer.conf').read_text()
    result = {'node': socket.gethostname(), 'job': os.environ['SLURM_JOB_ID'],
              'driver_sha256': SHA, 'started': time.time(), 'cases': []}
    for label, size, traced in [('plain-small', 512, False), ('trace-small', 512, True),
                                 ('plain-build-size', 32768, False), ('trace-build-size', 32768, True)]:
        case = BASE / 'cases' / label
        private(case)
        private(case / 'bin')
        overlay = case / 'work.ext3'
        with (case / 'create.log').open('w') as log:
            subprocess.run([APP, 'overlay', 'create', '--fakeroot', '--sparse', '--size',
                            str(size), str(overlay)], env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        if traced:
            trace_cmd = ['/usr/bin/strace', '-f', '-qq', '-ttt', '-T', '-yy', '-s', '64',
                         '-e', 'trace=fsync,fdatasync,pwrite64,write,close,exit_group',
                         '-o', str(case / 'driver.trace'), str(DRIVER)]
            wrapper = case / 'bin/fuse2fs'
            wrapper.write_text('#!/bin/sh\nexec ' + shlex.join(trace_cmd) + ' "$@"\n')
            wrapper.chmod(0o700)
            binary = case / 'bin'
        else:
            binary = DRIVER.parent
        config = case / 'apptainer.conf'
        config.write_text(site + '\nbinary path = ' + str(binary) + ':$PATH:/usr/bin:/bin:/usr/sbin:/sbin\n')
        workload = '''from pathlib import Path
import os,time,json
r=Path('/workspace/probe');r.mkdir(parents=True)
for n in range(2048):
    p=r/(str(n)+'.dat');p.write_bytes(bytes([n%256])*4096)
for n in range(0,2048,3):(r/(str(n)+'.dat')).unlink()
print('PAYLOAD_WRITES_COMPLETE',time.time(),flush=True)
t=time.monotonic()
with (r/'sync-marker').open('wb') as f:
    f.write(b'probe\\n');f.flush();os.fsync(f.fileno())
rec=dict(sync_seconds=time.monotonic()-t,completed_at=time.time())
Path('/results/sync.json').write_text(json.dumps(rec)+'\\n')
print('PAYLOAD_SYNC_COMPLETE',rec,flush=True)
'''
        argv = [APP, '--debug', '--config', str(config), 'exec', '--fakeroot', '--cleanenv',
                '--containall', '--no-home', '--no-mount', 'bind-paths,home,cwd,tmp,hostfs',
                '--pwd', '/', '--net', '--network', 'none']
        for name in ('/usr', '/sbin', '/lib', '/lib64', '/opt/devtools'):
            argv += ['--bind', name + ':' + name + ':ro']
        argv += ['--bind', str(case) + ':/results:rw', '--overlay', str(overlay),
                 '--env', 'TMPDIR=/workspace,PYTHONDONTWRITEBYTECODE=1', str(IMAGE),
                 '/usr/bin/python3', '-B', '-c', workload]
        record = dict(label=label, overlay_mb=size, traced=traced, started=time.time(), argv=argv)
        result['cases'].append(record)
        (BASE / 'result.json').write_text(json.dumps(result, indent=2)+'\n')
        with (case / 'apptainer.log').open('w') as log:
            p = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in p.stdout:
                log.write(str(time.time()) + ' ' + line)
                log.flush()
            record['returncode'] = p.wait()
        record['completed'] = time.time()
        text = (case / 'apptainer.log').read_text()
        record['cleanup_failure'] = 'Cleanup error: while stopping driver' in text
        record['timeout_warning'] = 'Terminating fuse2fs after timeout' in text
        record['signals'] = [s for s in text.splitlines() if any(k in s for k in
            ('PAYLOAD_', 'after wait timeout', 'Waiting for fuse2fs', 'exited with status', 'Cleanup error', 'Terminating fuse2fs'))]
        if (case / 'sync.json').exists():
            record['sync'] = json.loads((case / 'sync.json').read_text())
        with (case / 'fsck.log').open('w') as log:
            record['fsck_returncode'] = subprocess.run(['/usr/sbin/e2fsck', '-fn', str(overlay)],
                env=env, stdout=log, stderr=subprocess.STDOUT, timeout=90).returncode
        (BASE / 'result.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps({k:v for k,v in record.items() if k != 'argv'}), flush=True)
    result['completed'] = time.time()
    (BASE / 'result.json').write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__':
    main()
