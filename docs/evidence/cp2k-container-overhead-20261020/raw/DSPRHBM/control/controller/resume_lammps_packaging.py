#!/usr/bin/env python3
"""Clone a completed LAMMPS build overlay and retry only packaging/verify."""
import argparse, hashlib, json, os, re, shlex, subprocess, sys
from pathlib import Path

def digest(path):
    h = hashlib.sha256()
    with path.open('rb', buffering=0) as f:
        while True:
            block = f.read(16 * 1024 * 1024)
            if not block:
                return h.hexdigest()
            h.update(block)
def run(argv, **kw): return subprocess.run([str(x) for x in argv], check=True, text=True, **kw)
def main():
    p=argparse.ArgumentParser(); p.add_argument('old_run'); p.add_argument('new_run'); a=p.parse_args()
    for x in (a.old_run,a.new_run):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', x): raise ValueError('unsafe run id')
    root=Path(os.environ.get('SAI_SOFTWARE_ROOT', Path.home()/'sai-hpc-software')).resolve()/ 'experimental/deepmd-lammps'
    old=root/'runs'/a.old_run; new=root/'runs'/a.new_run
    if not old.is_dir(): raise ValueError('old run missing')
    if (old/'job.id').read_text().strip() != '1444533': raise ValueError('unexpected source job')
    overlay=old/'work.ext3'; before=digest(overlay)
    if new.exists():
        # A previous invocation may have completed the expensive copy but
        # failed before submission. Reuse it only after an exact hash check;
        # arbitrary or partial directories are rejected.
        if (new/'job.id').exists() or not (new/'work.ext3').is_file():
            raise ValueError('new run exists with a job or incomplete overlay')
        if digest(new/'work.ext3') != before:
            raise ValueError('existing copied overlay hash mismatch')
    else:
        for d in ('input','results','runtime','apptainer-cache'): (new/d).mkdir(parents=True, mode=0o700)
        run(['cp','--reflink=auto','--sparse=always','--preserve=mode,timestamps',overlay,new/'work.ext3'])
        if digest(overlay)!=before or digest(new/'work.ext3')!=before: raise RuntimeError('overlay hash changed')
    request=json.loads((old/'request.json').read_text())
    sys.path.insert(0, request['controller'])
    from md_controller import container_command
    from remote_controller import site_app_binds
    from md_tracking import fingerprint
    control=Path(request['controller'])
    if fingerprint(control) != request['delivery']['recipe_sha256']:
        raise ValueError('frozen recipe changed')
    pack=Path(__file__).resolve().with_name('md_packaging_recovery.sh')
    if not pack.is_file(): raise ValueError('packaging helper missing')
    script=new/'job.sbatch'
    identity=json.dumps(request['delivery'], sort_keys=True, separators=(',',':'))
    image=Path('/home/stardust/sai-hpc-software/containers/base/minimal-v1.sif')
    site_binds = list(site_app_binds('lammps'))
    command=container_command(image,['/usr/bin/bash','/packaging/md_packaging_recovery.sh','package',identity],
        control=control, overlay=new/'work.ext3', jobs=1, gpu=True,
        extra_binds=[*site_binds, (pack,'/packaging/md_packaging_recovery.sh')])
    verify=container_command(new/'result.sif',['/usr/bin/bash','/control/md_container_entry.sh','verify',identity],
        control=control, jobs=1, gpu=True, extra_binds=site_binds)
    extract=container_command(image,['/usr/bin/cat','/workspace/final.squashfs'],
        overlay=str(new/'work.ext3') + ':ro', jobs=1, gpu=False)
    (new/'job.sbatch').write_text('\n'.join(['#!/bin/bash','#SBATCH --partition=16V100','#SBATCH --qos=flood-1o2gpu',
        '#SBATCH --nodes=1','#SBATCH --ntasks=1','#SBATCH --gpus-per-node=1','#SBATCH --time=120',
        f'#SBATCH --output={new}/results/slurm-%j.log','#SBATCH --export=NIL','set -eo pipefail',
        'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""','source /etc/profile.d/lmod.sh',
        'module load apptainer/1.4.4','set -u',f'export TMPDIR={new}/runtime APPTAINER_TMPDIR={new}/runtime APPTAINER_CACHEDIR={new}/apptainer-cache',
        'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
        'test -s /home/stardust/sai-hpc-software/containers/base/minimal-v1.sif',
        shlex.join(command),
        shlex.join(extract)+' > '+str(new)+'/final.squashfs',
        'test -s '+str(new)+'/final.squashfs', 'apptainer sif new '+str(new)+'/result.sif',
        'apptainer sif add --datatype 4 --partfs 1 --parttype 2 --partarch 2 --groupid 1 '+str(new)+'/result.sif '+str(new)+'/final.squashfs',
        shlex.join(verify),'echo MD_PACKAGING_CANDIDATE_VERIFIED','']))
    run(['bash','-n',new/'job.sbatch']); run(['sbatch','--test-only',new/'job.sbatch'])
    job=run(['sbatch','--parsable',new/'job.sbatch'],capture_output=True).stdout.strip().split(';')[0]
    if not job.isdigit(): raise ValueError('invalid job receipt')
    (new/'job.id').write_text(job+'\n'); request['job']=job; request['job_script_sha256']=digest(new/'job.sbatch')
    (new/'request.json').write_text(json.dumps(request,sort_keys=True)+'\n')
    print(json.dumps({'job':job,'run':str(new),'overlay_sha256':before}))
if __name__=='__main__': main()
