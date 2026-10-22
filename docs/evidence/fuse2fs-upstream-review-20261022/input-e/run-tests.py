from pathlib import Path
import subprocess,json,os
root=Path(os.environ['FUSE_REVIEW_ROOT'])
rows=[]
for variant,suite,expected in [('master','norecovery',1),('master','inherited_lock',1),('norecovery','norecovery',0),('inherited-lock','inherited_lock',0),('combined','norecovery',0),('combined','inherited_lock',0)]:
 label=variant+'-'+suite
 argv=['/usr/bin/python3','-B',str(root/'input-e'/('fuse2fs_'+suite+'.py')),'--fuse2fs',str(root/'drivers-e'/variant/'fuse2fs'),'--work-dir',str(root/'tests-e'/label)]
 with (root/(label+'-e.log')).open('xb') as log:
  result=subprocess.run(argv,stdout=log,stderr=subprocess.STDOUT,timeout=240)
 rows.append(dict(variant=variant,suite=suite,returncode=result.returncode,expected_returncode=expected,passed=result.returncode==expected))
 (root/'tests-e-summary.json').write_text(json.dumps(rows,indent=2)+'\n')
 print(json.dumps(rows[-1]),flush=True)
if not all(row['passed'] for row in rows): raise SystemExit(1)
