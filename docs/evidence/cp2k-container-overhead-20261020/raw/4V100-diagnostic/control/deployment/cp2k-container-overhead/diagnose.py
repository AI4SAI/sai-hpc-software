import json,os,subprocess,sys,time
from pathlib import Path
sys.dont_write_bytecode=True
import run as common
root=Path(sys.argv[1]); request=json.loads((root/'request.json').read_text());reference=json.loads((root/'reference.json').read_text())
source=Path("/home/stardust/sai-hpc-software/runtime-tests/cp2k-container-overhead-20261020-4V100/fixtures/water-gpw.inp")
assert common.checksum(source)==request['inputs']['fixtures/water-gpw.inp']
result=[]
for threads,stack in [(4,None),(4,"256M"),(1,None)]:
 for mode in ["native","sif"]:
  label=f"omp{threads}-stack{stack or 'default'}-{mode}";folder=root/label;folder.mkdir()
  (folder/'input.inp').write_bytes(source.read_bytes())
  env=dict(os.environ,OMP_NUM_THREADS=str(threads),SAI_OVERHEAD_IMAGE=request['image'])
  if stack: env['OMP_STACKSIZE']=stack
  else: env.pop('OMP_STACKSIZE',None)
  cmd=['mpirun','-np','1','--map-by',os.environ['MAP_OPT'],'--report-bindings','bash',str(Path(__file__).with_name('rank.sh')),mode,'cp2k','-i','input.inp']
  start=time.monotonic()
  with (folder/'cp2k.log').open('x') as log: completed=subprocess.run(cmd,cwd=folder,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=180)
  elapsed=time.monotonic()-start;(folder/'elapsed.txt').write_text(str(elapsed)+'\n')
  item=dict(label=label,threads=threads,stack=stack,mode=mode,exit_code=completed.returncode,elapsed_seconds=elapsed)
  if completed.returncode==0:
   item['science']=common.parse(folder,'water-gpw',reference)
  result.append(item);common.write(root/'results/diagnosis.json',result);print(label,completed.returncode,flush=True)
