#!/usr/bin/env python3
"""Retain the exact numerical checks and independently reparse them offline."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shlex
import subprocess
import tarfile

PACKAGE = Path(__file__).resolve().parent
OUT = PACKAGE.parents[1] / 'docs/evidence/sif-lmod-20261024'
REMOTE = r'''
import io, os, subprocess, sys, tarfile
from pathlib import Path
root=Path('/org/org-open-sai/proj-sai-hpc-software')
jobs=['1919703','1919704','1919705','1919706','1919669','1919677','1919678','1919679','1919680','1919696','1919697','1919698','1919699']
with tarfile.open(fileobj=sys.stdout.buffer, mode='w|gz') as tar:
 for part in ('4V100','16V100','8V100V0','DSPRHBM'):
  run=root/('runtime-tests/sif-lmod-20261024-'+part+'-r4')
  for directory,dirs,files in os.walk(run):
   dirs[:]=[d for d in dirs if d!='tmp']
   for name in sorted(files):
    path=Path(directory)/name
    if not path.is_symlink():tar.add(path,arcname='raw/'+part+'/'+str(path.relative_to(run)),recursive=False)
 bundle=root/'sif/20261024-r4'
 for name in ('catalog.json','libexec/sif_runtime.py'):
  tar.add(bundle/name,arcname='raw/'+name,recursive=False)
 for name in ('check.py','check.sbatch','check-plan.json','deepmd-check.py'):
  tar.add(root/'deployment/sif-lmod-20261024-r4/deployment/sif-development-20261024'/name,
          arcname='raw/control/'+name,recursive=False)
 for part,rev,job in [('16V100','r1','1919669'),('DSPRHBM','r2','1919680'),('DSPRHBM','r3','1919696')]:
  run=root/('runtime-tests/sif-lmod-20261024-'+part+'-'+rev)
  for name in ('request.json','submission.json','cp2k/stdout.txt','slurm-'+job+'.log'):
   if rev=='r1' and name=='submission.json':continue  # first submission was issued directly
   tar.add(run/name,arcname='raw/earlier/'+job+'/'+name,recursive=False)
 text=subprocess.check_output(['sacct','-X','-n','-P','-j',','.join(jobs),'-o','JobID,State,ExitCode,Elapsed,Partition,NodeList,Account,WorkDir'],text=True)
 raw=text.encode();info=tarfile.TarInfo('raw/accounting.txt');info.size=len(raw);info.mode=0o444;tar.addfile(info,io.BytesIO(raw))
'''


def revalidate():
    inventory = json.loads((OUT / 'SHA256.json').read_text())
    for name, expected in inventory.items():
        assert hashlib.sha256((OUT / name).read_bytes()).hexdigest() == expected, name
    spec = importlib.util.spec_from_file_location('sif_check', PACKAGE / 'check.py')
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)
    reports = {}
    accounting = {line.split('|')[0]: line.split('|') for line in (OUT / 'raw/accounting.txt').read_text().splitlines() if line}
    for part in ('4V100','16V100','8V100V0','DSPRHBM'):
        output = OUT / 'raw' / part
        report = check.verify(output, catalog_override=OUT / 'raw/catalog.json', write=False)
        assert report == json.loads((output / 'verification.json').read_text())
        row = accounting[report['job']]
        assert row[1:3] == ['COMPLETED', '0:0'] and row[4] == part and row[6] == 'proj-sai-hpc-software'
        reports[part] = report
    result = dict(passed=True, checked_files=len(inventory), partitions=reports)
    (OUT / 'local-validation.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps(dict(passed=True, checked_files=len(inventory), jobs=[r['job'] for r in reports.values()])))


if __name__ == '__main__':
    import sys
    if '--fetch' in sys.argv:
        OUT.mkdir(parents=True, exist_ok=True)
        assert not (OUT / 'raw').exists(), 'preserve the existing archive'
        raw = subprocess.check_output(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=15','SAI-stardust',
                                       'python3 -c ' + shlex.quote(REMOTE)])
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as tar:
            tar.extractall(OUT, filter='data')
        inventory = {str(p.relative_to(OUT)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sorted((OUT/'raw').rglob('*')) if p.is_file()}
        (OUT / 'SHA256.json').write_text(json.dumps(inventory,indent=2,sort_keys=True)+'\n')
    revalidate()
