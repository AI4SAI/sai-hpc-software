"""Resume only the recorded fresh dependency/application/science recovery."""
from pathlib import Path
import hashlib,json,os,subprocess,sys,traceback

task=Path(sys.argv[1]);record=json.loads((task/'prepared.json').read_text())
root=Path(record['root']);control=Path(record['control']);run_id=record['run_id']
identity=record['identity'];run=root/'runs'/run_id;deps=run_id+'-dependencies'
os.environ.update(SAI_SOFTWARE_ROOT=str(root),PYTHONDONTWRITEBYTECODE='1')
assert hashlib.sha256((Path(record['original_run'])/'request.json').read_bytes()).hexdigest()==record['original_request_sha256']

def call(script,*args):
 command=['python3','-B',str(control/script),*map(str,args)]
 print('RECOVERY_COMMAND',json.dumps(command),flush=True)
 subprocess.run(command,cwd=root,check=True)

try:
 if not (root/'runs'/deps/'job.id').exists():
  call('cp2k_dependency_stage.py','submit',root,control,deps,json.dumps(identity,sort_keys=True),'--jobs','16','--minutes','720','--overlay-mb','16384')
 call('cp2k_dependency_stage.py','monitor',root,deps,'--timeout','43200')
 if not (run/'job.id').exists():
  call('software_controller.py','submit','cp2k',run_id,identity['source_sha'],identity['source_version'],'dsprhbm','--track','development','--source-ref','master','--dependencies-run',deps,'--jobs','16','--minutes','360','--overlay-mb','16384')
 call('software_controller.py','monitor',run_id,'--timeout','43200')
 science=run_id+'-science'
 if not (root/'runtime-tests'/science/'job.id').exists():
  call('cp2k_acceptance.py','submit',science,identity['source_version'],'dsprhbm','--build-run-id',run_id)
 call('cp2k_acceptance.py','monitor',science)
 call('software_controller.py','publish',run_id)
 result=dict(status='published',run_id=run_id,identity=identity,artifact=(run/'artifact.path').read_text().strip(),original_run=record['original_run'])
except Exception as error:
 result=dict(status='failed',run_id=run_id,error=str(error),traceback=traceback.format_exc())
 (task/'recovery-status.json').write_text(json.dumps(result,indent=2)+'\n')
 raise
else:
 (task/'recovery-status.json').write_text(json.dumps(result,indent=2)+'\n')
 print('RECOVERY_PUBLISHED',json.dumps(result),flush=True)
