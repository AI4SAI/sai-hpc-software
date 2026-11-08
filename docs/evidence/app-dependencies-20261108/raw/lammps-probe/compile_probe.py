import hashlib
import json
import os
from pathlib import Path
import resource
import shlex
import subprocess
import sys
import time

label=sys.argv[1]
text=Path('/workspace/lammps-build/CMakeFiles/lammps.dir/flags.make').read_text()
flags={}
for line in text.splitlines():
    if line.startswith(('CXX_DEFINES =','CXX_INCLUDES =','CXX_FLAGS =')):
        key,value=line.split('=',1);flags[key.strip()]=shlex.split(value)
out=Path('/runtime/objects');out.mkdir(exist_ok=True)
report={'label':label,'ld_preload':os.environ.get('LD_PRELOAD',''),
        'flags_sha256':hashlib.sha256(text.encode()).hexdigest(),'samples':[]}
for name in ('angle.cpp','pair.cpp','MANYBODY/pair_tersoff.cpp','KOKKOS/pair_lj_cut_kokkos.cpp'):
    source=Path('/workspace/lammps/src')/name
    obj=out/(source.name+'.o')
    obj.unlink(missing_ok=True)
    cmd=['/workspace/lammps/lib/kokkos/bin/nvcc_wrapper',
         *flags['CXX_DEFINES'],*flags['CXX_INCLUDES'],*flags['CXX_FLAGS'],
         '-o',str(obj),'-c',str(source)]
    start=time.monotonic();before=resource.getrusage(resource.RUSAGE_CHILDREN)
    p=subprocess.run(cmd,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    after=resource.getrusage(resource.RUSAGE_CHILDREN)
    row={'source':name,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
         'seconds':time.monotonic()-start,'cpu_seconds':after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime,
         'exit_code':p.returncode,'output_sha256':hashlib.sha256(obj.read_bytes()).hexdigest() if obj.exists() else None}
    report['samples'].append(row)
    (Path('/runtime')/(label+'-'+source.name+'.log')).write_text(p.stdout)
    (Path('/runtime')/(label+'.json')).write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(row),flush=True)
    if p.returncode:raise SystemExit(p.stdout[-6000:])
