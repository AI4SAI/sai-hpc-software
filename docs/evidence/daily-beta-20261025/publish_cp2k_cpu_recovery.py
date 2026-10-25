"""Wait for accepted CPU recovery, bind its provenance, then run the publisher."""
from pathlib import Path
import hashlib,json,os,subprocess,sys,time

task=Path(sys.argv[1])
recovery=json.loads((task/'prepared.json').read_text())
publisher=json.loads((task/'publisher-ready.json').read_text())
sys.path.insert(0,publisher['snapshot']+'/controller')
import beta_candidates
root=Path(recovery['root']);original=Path(recovery['original_run']);run=root/'runs'/recovery['run_id']
state=task/'recovery-status.json'
while not state.exists():time.sleep(30)
result=json.loads(state.read_text())
assert result['status']=='published',result
builds=json.loads((Path(publisher['snapshot'])/'builds.json').read_text())
source=next(row for row in builds if row['track']=='development' and row['target']=='dsprhbm')
assert source['source_sha']==recovery['source_sha']
accepted=beta_candidates.candidate('cp2k',run,source)
assert accepted['build_verified'] and accepted['scientific_verified']
original_job=(original/'job.id').read_text().strip()
accounting=subprocess.check_output(['sacct','-X','-n','-P','-j',original_job,'--format=JobIDRaw,State,ExitCode,Account,WorkDir'],text=True)
rows=[line.split('|') for line in accounting.splitlines() if line.split('|')[0]==original_job]
assert len(rows)==1 and rows[0][1]=='FAILED' and rows[0][3:5]==['proj-sai-hpc-software',str(original)]
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
assert digest(original/'request.json')==recovery['original_request_sha256']
record=dict(schema=1,source_run=original.name,source_request_sha256=digest(original/'request.json'),
            run_id=run.name,request_sha256=digest(run/'request.json'),original_job=original_job,
            original_accounting=accounting,recovery_controller=recovery['control'],
            artifact_sha256=accepted['sha256'],scientific_evidence=accepted['evidence'],
            scientific_evidence_sha256=accepted['evidence_sha256'],
            reason='fresh dependency rebuild after rejected corrupt checkpoint; explicit pre-teardown fsync and filesystem check')
proof=original/'results/build-recovery.json'
if proof.exists():assert json.loads(proof.read_text())==record
else:
 with proof.open('x') as out:out.write(json.dumps(record,indent=2)+'\n')
 proof.chmod(0o444)
selected,extra=beta_candidates.build_recovery('cp2k',original)
assert selected==run
print('CP2K_RECOVERY_REGISTERED',json.dumps(record),flush=True)
subprocess.run(publisher['command'],check=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
print('CP2K_RECOVERY_AND_BETA_COMPLETE',flush=True)
