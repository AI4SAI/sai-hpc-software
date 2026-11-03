import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time

ROOT = Path('/org/org-open-sai/proj-sai-hpc-software')
APP = '/opt/devtools/apptainer/1.4.4/bin/apptainer'
DRIVER = ROOT / 'cache/tools/fuse2fs-46c1a8c8f42ab2419ea88072e41d89903c453d93f52485b5baddea5ca238bb10/bin/fuse2fs'
IMAGE = ROOT / 'containers/base/minimal-v1.sif'
WORK = Path(sys.argv[1])

def cleanenv():
    env = {k: v for k, v in os.environ.items() if not k.startswith(('APPTAINER_', 'SINGULARITY_'))}
    env.update(TMPDIR=str(WORK/'runtime'), APPTAINER_TMPDIR=str(WORK/'runtime'),
               APPTAINER_CACHEDIR=str(WORK/'cache'), PYTHONDONTWRITEBYTECODE='1')
    env.pop('LD_PRELOAD', None)
    return env

def main():
    if len(sys.argv) == 2:
        WORK.mkdir(mode=0o700)
        for name in os.listxattr(WORK):
            if name in ('system.posix_acl_access', 'system.posix_acl_default'):
                os.removexattr(WORK, name)
        WORK.chmod(0o700)
        for name in ('runtime','cache','mount'):
            (WORK/name).mkdir(mode=0o700)
        subprocess.run([APP,'overlay','create','--fakeroot','--sparse','--size','512',str(WORK/'work.ext3')],
                       check=True,env=cleanenv())
        command=[APP,'exec','--fakeroot','--ignore-fakeroot-command','--no-home',
                 '--no-mount','home,cwd','--pwd','/','--bind',
                 '/usr,/sbin,/lib,/lib64,/opt,'+str(ROOT)+',/etc/passwd,/etc/group,/etc/subuid,/etc/subgid,/etc/nsswitch.conf',
                 str(IMAGE),'/usr/bin/python3','-B',str(Path(__file__).resolve()),str(WORK),'inside']
        with (WORK/'outer-command.json').open('w') as f:
            json.dump(command,f)
        return subprocess.run(command,env=cleanenv()).returncode
    env=cleanenv()
    mount=WORK/'mount'
    trace=['/usr/bin/strace','-D','-f','-qq','-ttt','-T','-e','trace=fsync,fdatasync,exit_group',
           '-e','inject=fsync:delay_enter=2s','-o',str(WORK/'driver.trace')]
    command=trace+[str(DRIVER),'-f','-o','fakeroot',str(WORK/'work.ext3'),str(mount)]
    record=dict(job=os.environ.get('SLURM_JOB_ID'),node=socket.gethostname(),command=command)
    success=False
    with (WORK/'driver.log').open('w') as log:
        driver=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT)
        record['driver_pid']=driver.pid
        try:
            deadline=time.monotonic()+60
            while not any(line.split()[4]==str(mount) for line in Path('/proc/self/mountinfo').read_text().splitlines()):
                if driver.poll() is not None or time.monotonic()>deadline:
                    raise RuntimeError('driver mount failed: '+(WORK/'driver.log').read_text())
                time.sleep(.1)
            print('DRIVER_MOUNTED',driver.pid,flush=True)
            common=[APP,'--debug','exec','--fakeroot','--ignore-fakeroot-command','--cleanenv','--containall',
                    '--no-home','--no-mount','bind-paths,home,cwd,tmp,hostfs','--pwd','/','--net','--network','none']
            for path in ('/usr','/sbin','/lib','/lib64','/opt/devtools'):
                common+=['--bind',path+':'+path+':ro']
            write='''from pathlib import Path
import os
r=Path('/workspace/probe');r.mkdir(parents=True)
for n in range(2048): (r/str(n)).write_bytes(bytes([n%256])*4096)
for n in range(0,2048,3): (r/str(n)).unlink()
with (r/'sync').open('wb') as f:
 f.write(b'finished');f.flush();os.fsync(f.fileno())
print('PAYLOAD_SYNC_COMPLETE',flush=True)
'''
            command=common+['--overlay',str(mount),'--env','TMPDIR=/workspace,PYTHONDONTWRITEBYTECODE=1',
                            str(IMAGE),'/usr/bin/python3','-B','-c',write]
            record['payload_command']=command
            with (WORK/'write.log').open('w') as output:
                record['payload_returncode']=subprocess.run(command,env=env,stdout=output,stderr=subprocess.STDOUT,timeout=120).returncode
            text=(WORK/'write.log').read_text()
            print('PAYLOAD_RESULT',record['payload_returncode'],text[-1800:],flush=True)
            if record['payload_returncode'] or 'PAYLOAD_SYNC_COMPLETE' not in text or 'Cleanup error:' in text:
                raise RuntimeError('inner payload failed')
            success=True
        finally:
            started=time.monotonic()
            result=subprocess.run(['/usr/bin/umount',str(mount)],env=env,text=True,capture_output=True,timeout=30)
            record['unmount']=dict(returncode=result.returncode,stdout=result.stdout,stderr=result.stderr)
            try:
                record['driver_returncode']=driver.wait(timeout=120)
            except subprocess.TimeoutExpired:
                driver.terminate()
                try:
                    driver.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    driver.kill();driver.wait()
                record['driver_timeout']=True
            record['unmount_and_wait_seconds']=time.monotonic()-started
            (WORK/'result.json').write_text(json.dumps(record,indent=2)+'\n')
            print('DRIVER_RESULT',json.dumps(record),flush=True)
        if success and record.get('driver_returncode')==0 and record['unmount']['returncode']==0:
            with (WORK/'fsck.log').open('w') as output:
                record['fsck_returncode']=subprocess.run(['/usr/sbin/e2fsck','-fn',str(WORK/'work.ext3')],stdout=output,stderr=subprocess.STDOUT,env=env,timeout=120).returncode
            record['verified']=record['fsck_returncode']==0
            (WORK/'result.json').write_text(json.dumps(record,indent=2)+'\n')
            return 0 if record['verified'] else 1
        return 1

raise SystemExit(main())
