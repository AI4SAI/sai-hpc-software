#!/usr/bin/env python3
"""Measure natural fuse2fs unmount in a private user/mount namespace."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import time

BASE = Path(__file__).resolve().parent
ROOT = Path('/org/org-open-sai/proj-sai-hpc-software')
SHA = '46c1a8c8f42ab2419ea88072e41d89903c453d93f52485b5baddea5ca238bb10'
DRIVER = ROOT / 'cache/tools' / ('fuse2fs-' + SHA) / 'bin/fuse2fs'
APP = '/opt/devtools/apptainer/1.4.4/bin/apptainer'


def private(path):
    path.mkdir(exist_ok=True)
    for key in os.listxattr(path):
        if key in ('system.posix_acl_access', 'system.posix_acl_default'):
            os.removexattr(path, key)
    path.chmod(0o700)


def inside(label):
    case = BASE / label
    mount = case / 'mount'
    # -D preserves fuse2fs as our direct child, so signals and wait status
    # belong to the driver, unlike an ordinary foreground strace wrapper.
    command = ['/usr/bin/strace', '-D', '-f', '-qq', '-ttt', '-T', '-yy', '-s', '32',
               '-e', 'trace=fsync,fdatasync,pwrite64,write,close,exit_group',
               '-o', str(case / 'driver.trace'), str(DRIVER), '-f', '-o', 'fakeroot',
               str(case / 'work.ext3'), str(mount)]
    record = dict(command=command, started=time.time(), mode='direct-unmount-with-60-second-grace')
    with (case / 'driver.log').open('w') as log:
        proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 40
            while not os.path.ismount(mount):
                if proc.poll() is not None:
                    raise RuntimeError('driver exited before mount: '+str(proc.returncode))
                if time.monotonic() >= deadline:
                    raise TimeoutError('mount exceeded 40 seconds')
                time.sleep(0.025)
            record['mounted_at'] = time.time()
            r = mount / 'upper/probe'
            r.mkdir(parents=True)
            for n in range(2048):
                (r / (str(n)+'.dat')).write_bytes(bytes([n % 256])*4096)
            for n in range(0,2048,3):
                (r / (str(n)+'.dat')).unlink()
            before = time.monotonic()
            with (r/'sync-marker').open('wb') as f:
                f.write(b'probe\n');f.flush();os.fsync(f.fileno())
            record['payload_sync_seconds'] = time.monotonic()-before
            record['payload_sync_complete'] = time.time()
            # Check data before unmount; no process holds a cwd or descriptor
            # inside the mount when umount is called.
            record['data_files'] = len(list(r.glob('*.dat')))
            record['unmount_start'] = time.time()
            unmount = subprocess.run(['/usr/bin/umount', str(mount)], text=True, capture_output=True, timeout=40)
            record['unmount'] = dict(returncode=unmount.returncode, stdout=unmount.stdout, stderr=unmount.stderr)
            unmount.check_returncode()
            record['unmount_return'] = time.time()
            proc.wait(timeout=60)
            record['driver_exit'] = time.time()
            record['driver_returncode'] = proc.returncode
            record['post_unmount_seconds'] = record['driver_exit']-record['unmount_return']
        except BaseException as exc:
            record['error'] = repr(exc)
            raise
        finally:
            if os.path.ismount(mount):
                subprocess.run(['/usr/bin/umount', str(mount)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill();proc.wait(timeout=10)
            (case/'result.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({k:v for k,v in record.items() if k!='command'}),flush=True)


def main():
    if len(sys.argv)>1:
        return inside(sys.argv[1])
    assert hashlib.sha256(DRIVER.read_bytes()).hexdigest()==SHA
    for name in ('runtime','cache'):
        private(BASE/name)
    env=dict(os.environ,TMPDIR=str(BASE/'runtime'),APPTAINER_TMPDIR=str(BASE/'runtime'),
             APPTAINER_CACHEDIR=str(BASE/'cache'),PYTHONDONTWRITEBYTECODE='1')
    for label in ('grace-a','grace-b'):
        case=BASE/label;private(case);private(case/'mount')
        with (case/'create.log').open('w') as log:
            subprocess.run([APP,'overlay','create','--fakeroot','--sparse','--size','512',str(case/'work.ext3')],
                           env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=90)
        with (case/'namespace.log').open('w') as log:
            status=subprocess.run(['/usr/bin/unshare','--user','--map-root-user','--mount','--propagation','private',
                                   '/usr/bin/python3','-B',str(Path(__file__).resolve()),label],env=env,stdout=log,
                                   stderr=subprocess.STDOUT,timeout=180).returncode
        if (case/'result.json').exists():
            record=json.loads((case/'result.json').read_text())
        else:
            record={}
        record['namespace_returncode']=status
        with (case/'fsck.log').open('w') as log:
            record['fsck_returncode']=subprocess.run(['/usr/sbin/e2fsck','-fn',str(case/'work.ext3')],
                env=env,stdout=log,stderr=subprocess.STDOUT,timeout=90).returncode
        (case/'result.json').write_text(json.dumps(record,indent=2)+'\n')
        print(label,json.dumps({k:v for k,v in record.items() if k!='command'}),flush=True)


if __name__ == '__main__':
    main()
