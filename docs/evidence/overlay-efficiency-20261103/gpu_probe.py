"""Exercise the complete NVIDIA-library check used by DeePMD/LAMMPS."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path('/org/org-open-sai/proj-sai-hpc-software')
WORK = Path(__file__).resolve().parent
CONTROL = WORK / 'controller'
sys.path.insert(0, str(CONTROL))
from remote_controller import container_command, private_scratch
job = os.environ['SLURM_JOB_ID']
run = ROOT / 'runs' / ('overlay-gpu-cache-' + job)
run.mkdir(mode=0o700)
for name in ('runtime', 'results', 'apptainer-cache'):
    private_scratch(run / name)
env = {k: v for k, v in os.environ.items()
       if not k.startswith(('APPTAINER_', 'SINGULARITY_'))}
env.pop('SAI_HPC_APPTAINER', None)
env.update(TMPDIR=str(run / 'runtime'), APPTAINER_TMPDIR=str(run / 'runtime'),
           APPTAINER_CACHEDIR=str(run / 'apptainer-cache'))
subprocess.run(['/opt/devtools/apptainer/1.4.4/bin/apptainer', 'overlay', 'create',
                '--fakeroot', '--sparse', '--size', '512', str(run / 'work.ext3')],
               check=True, env=env)
payload = '''import os,sys,json,ctypes
from pathlib import Path
sys.path.insert(0,'/bench/controller')
from md_driver_paths import normalize,REQUIRED
value,evidence=normalize(os.environ.get('LD_LIBRARY_PATH',''))
assert set(REQUIRED)<={item['name'] for item in evidence['libraries']}
cuda=ctypes.CDLL('libcuda.so.1');assert cuda.cuInit(0)==0
count=ctypes.c_int();assert cuda.cuDeviceGetCount(ctypes.byref(count))==0 and count.value>0
Path('/workspace/probe').mkdir(parents=True)
Path('/workspace/probe/complete').write_text('NVIDIA_DRIVER_CHECK_OK\\n')
Path('/results/driver-check.json').write_text(json.dumps(dict(cuda_devices=count.value,**evidence),indent=2)+'\\n')
print('NVIDIA_DRIVER_CHECK_OK',flush=True)
'''
args = container_command(str(ROOT / 'containers/base/minimal-v1.sif'),
    ['/usr/bin/python3', '-B', '-c', payload], overlay=str(run / 'work.ext3'),
    jobs=2, gpu=True, extra_binds=[(WORK, '/bench')])
args[2:2] = ['--bind', str(run / 'results') + ':/results:rw']
with (run / 'results/write.log').open('w') as stream:
    rc = subprocess.run(['/usr/bin/python3', '-B', str(CONTROL / 'cp2k_image_driver.py'),
        str(ROOT), str(run), *args[1:]], env=env, stdout=stream, stderr=subprocess.STDOUT).returncode
with (run / 'results/fsck.log').open('w') as stream:
    fsck = subprocess.run(['/usr/sbin/e2fsck', '-fn', str(run / 'work.ext3')],
        stdout=stream, stderr=subprocess.STDOUT).returncode
logs = (run / 'results/write.log').read_text()
item = dict(job=job, partition=os.environ['SLURM_JOB_PARTITION'], run=str(run), returncode=rc,
            fsck=fsck, driver_checked='NVIDIA_DRIVER_CHECK_OK' in logs,
            cache_warning='could not retrieve ld cache' in logs,
            receipts=[json.loads(p.read_text()) for p in (run/'results').glob('managed-overlay-*.json')])
item['passed'] = rc == 0 and fsck == 0 and item['driver_checked'] and not item['cache_warning']
(WORK/('gpu-probe-'+job+'.json')).write_text(json.dumps(item,indent=2)+'\n')
print(json.dumps(item),flush=True)
if not item['passed']:
    print(logs,flush=True)
    raise SystemExit(1)
