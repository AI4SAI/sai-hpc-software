#!/usr/bin/env python3
"""Check slow driver teardown, filesystem integrity and read-only remount data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import time

ROOT = Path('/org/org-open-sai/proj-sai-hpc-software')
DRIVER_SHA = '46c1a8c8f42ab2419ea88072e41d89903c453d93f52485b5baddea5ca238bb10'
DRIVER = ROOT / 'cache/tools' / ('fuse2fs-' + DRIVER_SHA) / 'bin/fuse2fs'
WRITE = '''from pathlib import Path
import os
r=Path('/workspace/probe'); r.mkdir(parents=True)
for n in range(2048): (r/str(n)).write_bytes(bytes([n%256])*4096)
for n in range(0,2048,3): (r/str(n)).unlink()
with (r/'sync').open('wb') as f:
 f.write(b'finished'); f.flush(); os.fsync(f.fileno())
print('PAYLOAD_SYNC_COMPLETE', flush=True)
'''
READ = '''from pathlib import Path
r=Path('/workspace/probe')
expected={str(n) for n in range(2048) if n%3} | {'sync'}
actual={p.name for p in r.iterdir()}
# fuse-overlayfs represents an opaque directory with this internal marker.
# It is visible when the saved upper directory is mounted as a read-only
# lower layer. All payload names (including deleted names) remain checked.
metadata=actual & {'.wh..wh..opq'}
for name in metadata: assert (r/name).lstat().st_size == 0
assert actual-metadata == expected, (sorted(actual-metadata-expected),sorted(expected-actual))
for n in range(2048):
 if n%3: assert (r/str(n)).read_bytes() == bytes([n%256])*4096
assert (r/'sync').read_bytes() == b'finished'
print('OVERLAY_METADATA',sorted(metadata),flush=True)
print('REMOUNT_DATA_VERIFIED 1365 files plus marker', flush=True)
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apptainer', required=True)
    p.add_argument('--work', required=True, type=Path)
    p.add_argument('--site-config', type=Path, default=Path('/opt/devtools/apptainer/1.4.4/etc/apptainer/apptainer.conf'))
    args = p.parse_args()
    work = args.work.resolve()
    if not work.is_relative_to(ROOT) or work.exists():
        raise ValueError('verification requires a new project directory')
    work.mkdir(mode=0o700, parents=True)
    # Match production private_scratch: project default ACLs must not be
    # copied into mkfs's upper/work directories or fakeroot UID mappings.
    for name in os.listxattr(work):
        if name in ('system.posix_acl_access', 'system.posix_acl_default'):
            os.removexattr(work, name)
    work.chmod(0o700)
    for name in ('runtime', 'cache'):
        (work / name).mkdir(mode=0o700)
    assert hashlib.sha256(DRIVER.read_bytes()).hexdigest() == DRIVER_SHA
    env = dict(os.environ, TMPDIR=str(work/'runtime'), APPTAINER_TMPDIR=str(work/'runtime'),
               APPTAINER_CACHEDIR=str(work/'cache'), PYTHONDONTWRITEBYTECODE='1')
    for name in ('APPTAINER_BIND', 'APPTAINER_BINDPATH', 'SINGULARITY_BIND', 'SINGULARITY_BINDPATH'):
        env.pop(name, None)
    app = str(Path(args.apptainer).resolve())
    site = args.site_config.read_text()
    record = dict(node=socket.gethostname(), job=os.environ.get('SLURM_JOB_ID'),
                  apptainer=app, binary_sha256=hashlib.sha256(Path(app).read_bytes()).hexdigest(),
                  starter_sha256=hashlib.sha256((Path(app).parents[1] / 'libexec/apptainer/bin/starter').read_bytes()).hexdigest(),
                  driver_sha256=DRIVER_SHA, started=time.time(), cases=[])
    success = True
    for label, size, delayed in [('delayed-fsync',512,True)]:
        case = work/label
        case.mkdir(mode=0o700)
        (case/'bin').mkdir(mode=0o700)
        overlay = case/'work.ext3'
        with (case/'create.log').open('w') as log:
            subprocess.run([app,'overlay','create','--fakeroot','--sparse','--size',str(size),str(overlay)],
                           env=env,check=True,stdout=log,stderr=subprocess.STDOUT)
        binary = DRIVER.parent
        if delayed:
            # -D leaves fuse2fs as the direct child observed by Apptainer.
            # Delay the final backing-file fsync too; do not change its result.
            trace = ['/usr/bin/strace','-D','-f','-qq','-ttt','-T','-e','trace=fsync,fdatasync,exit_group',
                     '-e','inject=fsync:delay_enter=2s','-o',str(case/'driver.trace'),str(DRIVER)]
            wrapper=case/'bin/fuse2fs'
            wrapper.write_text('#!/bin/sh\nexec '+shlex.join(trace)+' "$@"\n')
            wrapper.chmod(0o700)
            binary=wrapper.parent
        config=case/'apptainer.conf'
        config.write_text(site+'\nbinary path = '+str(binary)+':$PATH:/usr/bin:/bin:/usr/sbin:/sbin\n')
        common=[app,'--debug','--config',str(config),'exec','--fakeroot','--cleanenv','--containall',
                '--no-home','--no-mount','bind-paths,home,cwd,tmp,hostfs','--pwd','/','--net','--network','none']
        for path in ('/usr','/sbin','/lib','/lib64','/opt/devtools'):
            common+=['--bind',path+':'+path+':ro']
        common+=['--env','TMPDIR=/workspace,PYTHONDONTWRITEBYTECODE=1']
        image=str(ROOT/'containers/base/minimal-v1.sif')
        argv=[*common,'--overlay',str(overlay),image,'/usr/bin/python3','-B','-c',WRITE]
        started=time.monotonic()
        with (case/'write.log').open('w') as log:
            status=subprocess.run(argv,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=240).returncode
        text=(case/'write.log').read_text()
        item=dict(label=label,overlay_mb=size,delayed_fsync=delayed,write_returncode=status,
                  elapsed_seconds=time.monotonic()-started,
                  sync_complete='PAYLOAD_SYNC_COMPLETE' in text,
                  cleanup_failure='Cleanup error:' in text,
                  timeout_warning='Terminating fuse2fs after timeout' in text)
        if status or not item['sync_complete'] or item['cleanup_failure'] or item['timeout_warning']:
            item['data_verified'] = False
            record['cases'].append(item)
            record.update(completed=time.time(), verified=False)
            (work/'result.json').write_text(json.dumps(record,indent=2)+'\n')
            print(json.dumps(item),flush=True)
            return 1
        with (case/'fsck.log').open('w') as log:
            item['fsck_returncode']=subprocess.run(['/usr/sbin/e2fsck','-fn',str(overlay)],env=env,
                                                   stdout=log,stderr=subprocess.STDOUT,timeout=600).returncode
        # Preserve a failed overlay as evidence; never consume it as an input.
        if status==0 and item['sync_complete'] and not item['cleanup_failure'] and not item['timeout_warning'] and item['fsck_returncode']==0:
            argv=[*common,'--overlay',str(overlay)+':ro',image,'/usr/bin/python3','-B','-c',READ]
            with (case/'remount.log').open('w') as log:
                item['remount_returncode']=subprocess.run(argv,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=120).returncode
            item['data_verified']=item['remount_returncode']==0 and 'REMOUNT_DATA_VERIFIED' in (case/'remount.log').read_text()
        else:
            item['data_verified']=False
        success = success and item['data_verified']
        record['cases'].append(item)
        (work/'result.json').write_text(json.dumps(record,indent=2)+'\n')
        print(json.dumps(item),flush=True)
    record.update(completed=time.time(),verified=success)
    (work/'result.json').write_text(json.dumps(record,indent=2)+'\n')
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
