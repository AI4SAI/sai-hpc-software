import errno
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path('/org/org-open-sai/proj-sai-hpc-software')
WORK = Path(__file__).resolve().parent
CONTROL = WORK/'controller-v1'
sys.path.insert(0, str(CONTROL))
from remote_controller import private_scratch, container_command

APP = '/opt/devtools/apptainer/1.4.4/bin/apptainer'
job = os.environ['SLURM_JOB_ID']
run = ROOT/'runs'/('overlay-lock-'+job)
run.mkdir(mode=0o700)
for name in ('runtime','results','apptainer-cache'):
    private_scratch(run/name)
env = {k:v for k,v in os.environ.items() if not k.startswith(('APPTAINER_', 'SINGULARITY_'))}
env.update(TMPDIR=str(run/'runtime'),APPTAINER_TMPDIR=str(run/'runtime'),APPTAINER_CACHEDIR=str(run/'apptainer-cache'))
overlay = run/'work.ext3'
subprocess.run([APP,'overlay','create','--fakeroot','--sparse','--size','512',str(overlay)],env=env,check=True)
payload = '''from pathlib import Path
import time,sys,os
p=Path('/workspace/value');p.parent.mkdir(exist_ok=True);p.write_bytes(b'completed')
with p.open('rb+') as f:os.fsync(f.fileno())
Path('/results/ready').write_text('ready')
end=time.monotonic()+60
while not Path('/results/release').exists():
 if time.monotonic()>end:raise SystemExit(5)
 time.sleep(.1)
sys.stdout.buffer.write(bytes([0,255,13,10]))
'''
argv = container_command(str(ROOT/'containers/base/minimal-v1.sif'),
    ['/usr/bin/python3','-B','-c',payload],overlay=str(overlay))[1:]
argv[1:1] = ['--bind',str(run/'results')+':/results:rw']
command = ['/usr/bin/python3','-B',str(CONTROL/'cp2k_image_driver.py'),str(ROOT),str(run),*argv]
record = dict(job=job,run=str(run),competing_writer_rejected=False,lock_released=False)
with (run/'results/stdout.bin').open('wb') as stdout, (run/'results/stderr.log').open('w') as stderr:
    process = subprocess.Popen(command,env=env,stdout=stdout,stderr=stderr)
    try:
        deadline = time.monotonic()+90
        while not (run/'results/ready').exists():
            if process.poll() is not None or time.monotonic()>deadline:
                raise RuntimeError('payload failed to reach barrier')
            time.sleep(.1)
        with overlay.open('rb+') as image:
            try:
                fcntl.lockf(image,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError as error:
                if error.errno not in (errno.EAGAIN,errno.EACCES):raise
                record['competing_writer_rejected']=True
            else:
                fcntl.lockf(image,fcntl.LOCK_UN)
    finally:
        (run/'results/release').write_text('release')
        record['returncode']=process.wait(timeout=180)
record['binary_stdout_preserved']=(run/'results/stdout.bin').read_bytes()==bytes([0,255,13,10])
with overlay.open('rb+') as image:
    fcntl.lockf(image,fcntl.LOCK_EX|fcntl.LOCK_NB)
    record['lock_released']=True
    fcntl.lockf(image,fcntl.LOCK_UN)
record['passed']=(record['returncode']==0 and record['competing_writer_rejected'] and
                  record['lock_released'] and record['binary_stdout_preserved'])
(WORK/('locking-'+job+'.json')).write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record),flush=True)
raise SystemExit(0 if record['passed'] else 1)
