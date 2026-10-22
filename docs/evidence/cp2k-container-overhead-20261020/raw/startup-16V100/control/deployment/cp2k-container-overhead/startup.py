#!/usr/bin/env python3
"""Measure rank lifetime separately from host MPI startup/teardown noise."""
import argparse
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time

sys.dont_write_bytecode = True
import run as common

REPEATS = 20
MARKER = 'CP2K_RANK_STARTUP_PASSED'


def order():
    for mode in ['native','sif']: yield 'warmup',mode
    for index in range(REPEATS):
        for mode in (['native','sif'] if index%2==0 else ['sif','native']):
            yield str(index),mode


def sample(folder, reference):
    result = common.parse(folder,'version',reference)
    values = (folder/'rank-time.txt').read_text().split()
    common.require(len(values)==4,'Missing GNU time fields')
    result.update(rank_wall_seconds=float(values[0]),rank_user_seconds=float(values[1]),
                  rank_system_seconds=float(values[2]),rank_max_rss_kib=int(values[3]))
    common.require(result['rank_wall_seconds']>0,'Nonpositive rank lifetime')
    return result


def summary(samples):
    result={}
    for metric in ['elapsed_seconds','rank_wall_seconds','rank_user_seconds','rank_system_seconds']:
        values={mode:[samples[str(index)+'/'+mode][metric] for index in range(REPEATS)] for mode in ['native','sif']}
        deltas=[b-a for a,b in zip(values['native'],values['sif'])]
        result[metric]=dict(native_median=statistics.median(values['native']),sif_median=statistics.median(values['sif']),
                            paired_delta_median=statistics.median(deltas),paired_delta_mean=statistics.mean(deltas),
                            paired_delta_min=min(deltas),paired_delta_max=max(deltas),paired_deltas=deltas,values=values)
    return result


def execute(root):
    request=json.loads((root/'request.json').read_text())
    common.require(os.getuid()==request['uid']!=0 and os.geteuid()==os.getuid(),'Unexpected UID')
    common.require(os.environ['SAI_CP2K_PREFIX']==request['prefix'],'Wrong installed module')
    common.require(common.signature(Path(request['image']))==request['image_stat'],'SIF changed')
    for name,digest in request['inputs'].items(): common.require(common.checksum(root/name)==digest,'Input changed: '+name)
    environment=dict(os.environ,SAI_OVERHEAD_IMAGE=request['image'])
    rank=Path(__file__).with_name('rank.sh')
    mpi=['mpirun','-np','1','--map-by',os.environ['MAP_OPT'],'--report-bindings']
    samples={}
    reference=json.loads((root/'reference.json').read_text())
    for repeat,mode in order():
        folder=root/'cases'/repeat/mode; folder.mkdir(parents=True)
        command=mpi+['/usr/bin/time','-o','rank-time.txt','-f','%e %U %S %M','bash',str(rank),mode,'cp2k','--version']
        common.write(folder/'command.json',command)
        started=time.monotonic()
        with (folder/'cp2k.log').open('x') as log:
            subprocess.run(command,cwd=folder,env=environment,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120)
        (folder/'elapsed.txt').write_text(str(time.monotonic()-started)+'\n')
        samples[repeat+'/'+mode]=sample(folder,reference)
        print(repeat,mode,samples[repeat+'/'+mode]['rank_wall_seconds'],flush=True)
    for mode in ['native','sif']:
        folder=root/'probes'/mode; folder.mkdir(parents=True)
        info=folder.stat(); common.write(folder/'host.json',dict(uid=os.getuid(),dev=info.st_dev,inode=info.st_ino))
        shutil.copyfile(Path(__file__).with_name('probe.py'),folder/'probe.py')
        with (folder/'cp2k.log').open('x') as log:
            subprocess.run(mpi+['bash',str(rank),mode,'probe'],cwd=folder,env=environment,
                           stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120)
    probes=common.check_probes(root,request)
    common.require(common.signature(Path(request['image']))==request['image_stat'],'SIF changed during run')
    result=dict(passed=True,request=request,samples=samples,summary=summary(samples),probes=probes)
    result['files']={str(p.relative_to(root)):common.checksum(p) for p in root.rglob('*') if p.is_file()
                     and (p.suffix in ['.json','.txt','.tsv'] or p.name=='cp2k.log')}
    common.write(root/'results/evidence.json',result)
    (root/'results/passed.txt').write_text(MARKER+'\n')
    print(MARKER,flush=True)


def submit(parent,name):
    root=common.PROJECT/'runtime-tests'/('cp2k-container-startup-'+name)
    common.require(not root.exists() and root.parent==common.PROJECT/'runtime-tests','New run directory required')
    request=json.loads((parent/'request.json').read_text())
    common.require(os.getuid()==request['uid']!=0,'Wrong ordinary account')
    root.mkdir(); (root/'results').mkdir()
    shutil.copytree(parent/'control',root/'control')
    location=root/'control/deployment/cp2k-container-overhead'
    shutil.copyfile(Path(__file__),location/'startup.py')
    script=(location/'job.sbatch').read_text().replace('#SBATCH --time=01:30:00','#SBATCH --time=00:15:00')
    script=script.replace('/run.py" execute','/startup.py" execute')
    (location/'startup.sbatch').write_text(script)
    for name in ['reference.json','receipt.json','release.json','sidecar.json']: shutil.copyfile(parent/name,root/name)
    request.update(profile='rank-startup',parent_run=str(parent),paired_repeats=REPEATS,
                   inputs={str(p.relative_to(root)):common.checksum(p) for p in root.rglob('*') if p.is_file()})
    common.write(root/'request.json',request)
    host=(parent/'results/hostname.txt').read_text().strip().split('.')[0]
    command=['sbatch','--parsable','--partition='+request['partition'],'--qos=flood-1o2gpu','--gpus-per-node=1',
             '--exclude='+host,'--job-name=cp2k-rank-startup','--output='+str(root/'results/slurm-%j.log'),
             str(location/'startup.sbatch'),str(root)]
    job=subprocess.check_output(command,text=True).strip().split(';')[0]
    (root/'job.id').write_text(job+'\n'); common.write(root/'submission.json',dict(command=command,job=job))
    print(json.dumps(dict(job=job,run=str(root))))


def verify(root):
    request=json.loads((root/'request.json').read_text()); recorded=json.loads((root/'results/evidence.json').read_text())
    common.require(recorded['request']==request and recorded['passed'],'Recorded identity differs')
    for name,digest in {**request['inputs'],**recorded['files']}.items(): common.require(common.checksum(root/name)==digest,'Evidence changed: '+name)
    common.require((root/'results/passed.txt').read_text().strip()==MARKER,'Missing pass marker')
    job=(root/'job.id').read_text().strip()
    fields=next(line.split('|') for line in (root/'results/accounting.txt').read_text().splitlines() if line.startswith(job+'|'))
    common.require(fields[1]==request['partition'] and fields[2:4]==['COMPLETED','0:0'],'Job did not complete')
    common.require(common.check_probes(root,request)==recorded['probes'],'Provenance differs')
    reference=json.loads((root/'reference.json').read_text())
    samples={repeat+'/'+mode:sample(root/'cases'/repeat/mode,reference) for repeat,mode in order()}
    common.require(samples==recorded['samples'] and summary(samples)==recorded['summary'],'Raw timing reparse differs')
    return dict(passed=True,job=job,partition=fields[1],node=fields[5],state=fields[2],exit_code=fields[3],
                user=request['user'],uid=request['uid'],repetitions=REPEATS,summary=summary(samples))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('op',choices=['submit','execute','verify'])
    parser.add_argument('run',type=Path); parser.add_argument('--name',default='20261020-16V100')
    args=parser.parse_args()
    if args.op=='submit': submit(args.run,args.name)
    elif args.op=='execute': execute(args.run)
    else: print(json.dumps(verify(args.run),indent=2,sort_keys=True))
