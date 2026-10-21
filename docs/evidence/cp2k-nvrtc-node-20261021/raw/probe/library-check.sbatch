#!/bin/bash
#SBATCH --partition=4V100
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --qos=rush-1o2gpu
#SBATCH --time=00:05:00
set -e
base=${1:?}
export TMPDIR=$base/library-check-runtime
mkdir -p "$TMPDIR"
python3 -B - "$base" <<'PY'
from pathlib import Path
import hashlib,json,sys,socket,subprocess
base=Path(sys.argv[1])
ref=(base/'known-good-nvrtc/libnvrtc.so.12.9.86').read_bytes()
p=Path('/opt/devtools/nvidia/cuda-12.9.1/targets/x86_64-linux/lib/libnvrtc.so.12.9.86')
bad=p.read_bytes()
r={'host':socket.gethostname(),'reference_size':len(ref),'node_size':len(bad),'reference_sha256':hashlib.sha256(ref).hexdigest(),'node_sha256':hashlib.sha256(bad).hexdigest()}
changes=[];count=0
for start in range(0,min(len(ref),len(bad)),65536):
 a=ref[start:start+65536]; b=bad[start:start+65536]
 if a==b: continue
 for offset,(x,y) in enumerate(zip(a,b),start):
  if x!=y:
   count+=1
   if len(changes)<32: changes.append({'offset':offset,'expected':x,'actual':y})
r.update(differing_bytes=count,first_differences=changes)
r['libraries']=[]
for root in [Path('/opt/devtools/nvidia/cuda-12.9.1/lib64'),Path('/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/cuda/12.9/lib64')]:
 for name in ['libnvrtc.so.12.9.86','libnvrtc-builtins.so.12.9.86']:
  q=root/name
  r['libraries'].append({'path':str(q),'resolved':str(q.resolve()),'sha256':hashlib.sha256(q.read_bytes()).hexdigest(),'build_id':subprocess.check_output(['readelf','-n',str(q)],text=True).strip()})
print(json.dumps(r,indent=2))
(base/'n06-byte-comparison.json').write_text(json.dumps(r,indent=2)+'\n')
PY
