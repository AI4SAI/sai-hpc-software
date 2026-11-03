import os,json,sys,subprocess,hashlib,socket,time
from pathlib import Path
ROOT=Path('/org/org-open-sai/proj-sai-hpc-software')
APP='/opt/devtools/apptainer/1.4.4/bin/apptainer'
BASE=ROOT/'containers/base/minimal-v1.sif'
WORK=Path(__file__).resolve().parent
CONTROL=WORK/'controller-v1'
sys.path.insert(0,str(CONTROL))
from remote_controller import container_command, private_scratch
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

EXTRA="\nimport concurrent.futures\nimport subprocess\nr=Path('/workspace/compile');r.mkdir()\ngcc='/opt/devtools/gcc/14.3.0/bin/gcc'\nfor n in range(32): (r/f'f{n}.c').write_text(f'int f{n}(void){{return {n};}}\\n')\ndef compile_one(n):\n subprocess.run([gcc,'-O2','-c',str(r/f'f{n}.c'),'-o',str(r/f'f{n}.o')],check=True)\nwith concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool: list(pool.map(compile_one,range(32)))\nmain=''.join(f'int f{n}(void);\\n' for n in range(32))+'int main(void){return ('+'+'.join(f'f{n}()' for n in range(32))+')!=496;}\\n'\n(r/'main.c').write_text(main)\nsubprocess.run([gcc,str(r/'main.c'),*(str(r/f'f{n}.o') for n in range(32)),'-o',str(r/'sum')],check=True)\nsubprocess.run([str(r/'sum')],check=True)\nassert os.environ.get('SAI_OVERLAY_SENTINEL')=='kept-through-manager'\nprint('COMPILE_AND_ENV_VERIFIED',flush=True)\nif os.environ.get('SAI_TEST_GPU')=='1':\n import ctypes\n cuda=ctypes.CDLL('libcuda.so.1')\n assert cuda.cuInit(0)==0\n count=ctypes.c_int()\n assert cuda.cuDeviceGetCount(ctypes.byref(count))==0 and count.value>0\n print('CUDA_DEVICES',count.value,flush=True)\n"

job=os.environ['SLURM_JOB_ID']
gpu=os.environ.get('SLURM_JOB_PARTITION')!='DSPRHBM'
record=dict(job=job,node=socket.gethostname(),partition=os.environ.get('SLURM_JOB_PARTITION'),cases=[],
 apptainer_sha256=hashlib.sha256(Path(APP).read_bytes()).hexdigest(),
 starter_sha256=hashlib.sha256(Path('/opt/devtools/apptainer/1.4.4/libexec/apptainer/bin/starter').read_bytes()).hexdigest())
summary=WORK/('qualification-'+job+'.json')
for label,size,delayed,fail in [('small',512,False,False),('build-size',32768,False,False),('slow-fsync',512,True,False),('payload-failure',512,False,True)]:
 run=ROOT/'runs'/('overlay-qual-'+job+'-'+label)
 run.mkdir(mode=0o700)
 for name in ('runtime','results','apptainer-cache'):private_scratch(run/name)
 env={k:v for k,v in os.environ.items() if not k.startswith(('APPTAINER_','SINGULARITY_'))}
 env.update(TMPDIR=str(run/'runtime'),APPTAINER_TMPDIR=str(run/'runtime'),APPTAINER_CACHEDIR=str(run/'apptainer-cache'),
  APPTAINERENV_SAI_OVERLAY_SENTINEL='kept-through-manager')
 env.pop('SAI_HPC_APPTAINER',None)
 subprocess.run([APP,'overlay','create','--fakeroot','--sparse','--size',str(size),str(run/'work.ext3')],check=True,env=env)
 control=WORK/('controller-trace-v1' if delayed else 'controller-v1')
 payload=WRITE+EXTRA+('\nraise SystemExit(37)\n' if fail else '')
 argv=container_command(str(BASE),['/usr/bin/python3','-B','-c',payload],overlay=str(run/'work.ext3'),jobs=4,gpu=gpu)
 argv[2:2]=['--bind',str(run/'results')+':/results:rw','--env','SAI_TEST_GPU='+('1' if gpu else '0'),
  '--env','LD_LIBRARY_PATH=/opt/devtools/gcc/14.3.0/lib64']
 command=['/usr/bin/python3','-B',str(control/'cp2k_image_driver.py'),str(ROOT),str(run),*argv[1:]]
 started=time.monotonic()
 with (run/'results/write.log').open('w') as log:
  status=subprocess.run(command,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=360).returncode
 logs=(run/'results/write.log').read_text()
 receipts=[json.loads(p.read_text()) for p in (run/'results').glob('managed-overlay-*.json')]
 item=dict(label=label,run=str(run),status=status,seconds=time.monotonic()-started,delayed=delayed,
  managed_receipts=receipts,sync_complete='PAYLOAD_SYNC_COMPLETE' in logs,compiled='COMPILE_AND_ENV_VERIFIED' in logs,
  cuda=('CUDA_DEVICES' in logs if gpu else None),warnings='Cleanup error:' in logs or 'after timeout' in logs)
 good=(status==(37 if fail else 0) and len(receipts)==1 and receipts[0].get('completed') and
       receipts[0].get('driver_returncode')==0 and receipts[0].get('unmount_returncode')==0 and not receipts[0].get('forced_stop')
       and item['sync_complete'] and item['compiled'] and not item['warnings'] and (not gpu or item['cuda']))
 if good:
  with (run/'results/fsck.log').open('w') as log:
   item['fsck']=subprocess.run(['/usr/sbin/e2fsck','-fn',str(run/'work.ext3')],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=600).returncode
  if item['fsck']==0:
   read=READ+"\nimport subprocess;subprocess.run(['/workspace/compile/sum'],check=True)\n"
   argv=container_command(str(BASE),['/usr/bin/python3','-B','-c',read],overlay=str(run/'work.ext3')+':ro',jobs=4,gpu=False)
   command=['/usr/bin/python3','-B',str(CONTROL/'cp2k_image_driver.py'),str(ROOT),str(run),*argv[1:]]
   with (run/'results/read.log').open('w') as log:
    item['read_status']=subprocess.run(command,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=120).returncode
   item['data_verified']=item['read_status']==0 and 'REMOUNT_DATA_VERIFIED' in (run/'results/read.log').read_text()
  good=good and item['fsck']==0 and item.get('data_verified',False)
 item['passed']=bool(good)
 record['cases'].append(item)
 record['passed']=all(c['passed'] for c in record['cases'])
 summary.write_text(json.dumps(record,indent=2)+'\n')
 print(json.dumps(item),flush=True)
 if not good:
  print(logs[-7000:],flush=True)
  raise SystemExit(1)
