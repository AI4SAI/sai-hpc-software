"""Prepare a fresh source-pinned CPU recovery with the overlay sync repair."""
from pathlib import Path
import hashlib,json,shlex,subprocess,sys
inputs=Path(__file__).resolve().parent
checkout=inputs.parent/'md-independent-20261022'
revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=checkout,text=True).strip()
name='cp2k-dsprhbm-sync-recovery-20261025-1db19ea511d1'
root='/org/org-open-sai/proj-sai-hpc-software'
control=f'{root}/controller/{revision}/{name}'
task=f'{root}/operations/{name}'
archive=inputs/(name+'-'+revision[:12]+'.tar.gz')
with archive.open('wb') as out:
 subprocess.run(['git','archive','--format=tar.gz',revision,'controller'],cwd=checkout,stdout=out,check=True)
ssh=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=15','SAI-stardust']
subprocess.run(ssh+[shlex.join(['mkdir','-p',control,task])],check=True)
subprocess.run(['scp','-q','-o','BatchMode=yes','-o','ConnectTimeout=15',str(archive),'SAI-stardust:'+task+'/controller.tar.gz'],check=True)
setup=r'''from pathlib import Path
import hashlib,json,os,shlex,subprocess,sys,tarfile
record=json.loads(sys.argv[1]);root=Path(record['root']);control=Path(record['control']);task=Path(record['task'])
archive=task/'controller.tar.gz'
assert hashlib.sha256(archive.read_bytes()).hexdigest()==record['archive_sha256']
with tarfile.open(archive) as tar:
 for member in tar.getmembers():
  if member.isdir():continue
  assert member.isfile() and member.name.startswith('controller/')
  relative=Path(member.name).relative_to('controller')
  assert '..' not in relative.parts
  path=control/relative;data=tar.extractfile(member).read()
  if path.exists():assert path.read_bytes()==data
  else:
   path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);path.chmod(0o444)
alias=control/'cp2k'
if not alias.exists():alias.write_bytes((control/'cp2k_runtime.sh').read_bytes());alias.chmod(0o555)
env=dict(os.environ,SAI_SOFTWARE_ROOT=str(root),PYTHONDONTWRITEBYTECODE='1')
cli=['python3','-B',str(control/'software_controller.py')]
old=root/'runs/40189124444-1-development-dsprhbm-2026-10-25-1db19ea511d1'
original=json.loads((old/'request.json').read_text())
identity=json.loads(subprocess.check_output(cli+['identity','cp2k',record['source_sha'],'master-2026-10-25','dsprhbm','--track','development','--source-ref','master'],env=env,text=True))
for key in ('software','target','source_sha','source_ref','source_version','track'):
 assert identity[key]==original['identity'][key]
run=root/'runs'/record['run_id']
subprocess.run(cli+['init','cp2k',record['run_id']],env=env,check=True,stdout=subprocess.DEVNULL)
updates=run/'input/cp2k-updates';updates.mkdir(exist_ok=True)
for path in (old/'input/cp2k-updates').iterdir():
 assert path.is_file() and not path.is_symlink()
 dest=updates/path.name
 if dest.exists():assert hashlib.sha256(dest.read_bytes()).digest()==hashlib.sha256(path.read_bytes()).digest()
 else:
  import shutil
  shutil.copyfile(path,dest)
  assert hashlib.sha256(dest.read_bytes()).digest()==hashlib.sha256(path.read_bytes()).digest()
record.update(identity=identity,original_run=str(old),original_request_sha256=hashlib.sha256((old/'request.json').read_bytes()).hexdigest(),original_overlay_reused=False)
prepared=task/'prepared.json'
if prepared.exists():assert json.loads(prepared.read_text())==record
else:prepared.write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record))
'''
record=dict(root=root,control=control,task=task,revision=revision,run_id=name,source_sha='1db19ea511d10fbf2eb75cec7c0d88b6635563e2',archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
result=subprocess.check_output(ssh+[shlex.join(['python3','-c',setup,json.dumps(record)])],text=True)
record=json.loads(result.splitlines()[-1])
(inputs/'cp2k-cpu-recovery-ready.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record,indent=2))
