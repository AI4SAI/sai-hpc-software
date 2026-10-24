import json,os
from pathlib import Path
r=json.loads(Path('ready.json').read_text());found=[]
for p in Path('/proc').iterdir():
 try:
  if p.name.isdigit() and p.stat().st_uid==os.getuid() and os.readlink(p/'ns/mnt')==r['memory_scope']['mount']:found.append(p.name)
 except OSError:pass
visible=[p for p in r['sentinels'] if Path(p).exists()]
assert not found and not visible
Path('host-after.json').write_text(json.dumps(dict(namespace_processes=found,host_visible_sentinels=visible,observer_job=os.environ['SLURM_JOB_ID']),indent=2)+'\n')
