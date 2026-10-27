#!/usr/bin/env python3
"""Freeze an exact deployed release, its SIF and paired benchmark inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parents[1]
HOME_RELEASES = Path('/home/stardust/sai-hpc-software')
PROJECT = Path('/org/org-open-sai/proj-sai-hpc-software')
sys.path.insert(0, str(REPO / 'controller'))
from sif_runtime import digest


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def prepare(root, software, partition):
    assert root.is_relative_to(PROJECT) and root.resolve() == root
    root.mkdir(parents=True)
    software_dir = {'cp2k':'cp2k-2026.2-shared-elpa', 'abacus':'abacus-3.11.0-beta11',
                    'lammps':'lammps-30Sep2026', 'deepmd-kit':'lammps-30Sep2026'}[software]
    deployment = HOME_RELEASES / 'deployment' / software_dir
    release = read(deployment / 'release.json')
    delivery = next(d for d in release['deliveries'] if d['partition'] == partition)
    plan = dict(schema=1, software=software, partition=partition, repeats=3,
                release_file=str(deployment/'release.json'), release_sha256=digest(deployment/'release.json'),
                references={}, cases=[], probe_files={})
    if software == 'cp2k':
        image = HOME_RELEASES/'containers/software/cp2k/release'/delivery['build_id']/partition/(delivery['run_id']+'.sif')
        proof = image.with_suffix('.json')
        manifest = read(proof)
        assert manifest['build_verified'] and manifest['cp2k_science']['verified']
        identity = manifest['identity']
        binary = Path(identity['install_prefix'])/'bin/cp2k.psmp'
        science = HOME_RELEASES/'runtime-tests'/manifest['cp2k_science']['run_id']
        evidence = read(science/'results/evidence.json')
        plan.update(cases=['water-gpw','water64-gpw','water64-elpa'], program='cp2k',
                    module='cp2k/2026.2-auto',startup=['--version'])
        for case in plan['cases']:
            shutil.copytree(science/'fixtures'/case,root/'inputs'/case)
            plan['references'][case] = evidence['samples'][case+'/single']
        binds=[]
    else:
        image = HOME_RELEASES/delivery['image']
        installed = next(i for i in delivery['installations'] if i['identity']['software']==software)
        identity = installed['identity']
        binary = Path(identity['install_prefix'])/'bin'/({'abacus':'abacus','lammps':'lmp','deepmd-kit':'python'}[software])
        proof = HOME_RELEASES/delivery['publication']
        assert proof.is_file() and digest(proof)==delivery['publication_sha256']
        if software=='abacus':
            reference=read(deployment/'native-reference.json')['partitions'][partition]
            plan.update(cases=['pw','hse','deepks'],program='abacus',
                        module='abacus/3.11.0-beta11-auto',startup=['--version'])
            for case in plan['cases']:
                for relative,record in reference['cases'][case]['inputs'].items():
                    source=HOME_RELEASES/record['source']
                    assert digest(source)==record['sha256']
                    destination=root/'inputs'/case/relative
                    destination.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(source,destination)
                if case=='pw':
                    p=root/'inputs'/case/'INPUT'
                    p.write_text(re.sub(r'^[ \t]*kpar[ \t]+\S+[^\n]*\n?', '',p.read_text(),flags=re.M)+'\nkpar 1\n')
                plan['references'][case]=reference['cases'][case]['energy_ev']
            binds=['/opt/apps/conda_env/deepmd-kit-3.1.2']
        else:
            reference=read(deployment/'native-reference.json')['partitions'][partition]
            case='deepmd-pt-2058'
            plan.update(cases=[case],program='lmp' if software=='lammps' else 'deepmd-python',
                        module='lammps/30Sep2026-deepmd320-auto',
                        startup=['-h'] if software=='lammps' else ['-c','import deepmd; print(deepmd.__version__)'])
            for relative in ('data.lmp','model.pth','fixture.json','plumed.dat'):
                source=HOME_RELEASES/reference['science_case']/relative
                assert digest(source)==reference['case_files'][relative]
                destination=root/'inputs'/case/relative
                destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source,destination)
            binds=['/opt/apps/conda_env/deepmd-kit-3.2.0','/opt/apps/conda_env/VeloxChem/gpu',
                   '/opt/apps/plumed/plumed-2.10.1']
    prefix=Path(identity['install_prefix'])
    assert image.is_file() and digest(image)==delivery['image_sha256']
    program_file = prefix/'lib/python3.13/site-packages/deepmd/__init__.py' if software=='deepmd-kit' else binary
    plan['application_file']=str(program_file)
    # Pin shipped ELF/private library bytes and Python implementation evidence.
    files={str(program_file):digest(program_file)}
    for sub in ('lib','lib64','dependencies/nep/lib'):
        for p in (prefix/sub).glob('*.so*'):
            if p.is_file():files[str(p)]=digest(p)
    if software=='deepmd-kit':
        for relative in ('pt/train/training.py','pt/infer/deep_eval.py'):
            p=prefix/'lib/python3.13/site-packages/deepmd'/relative
            if p.exists():files[str(p)]=digest(p)
    mpi='/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-'+identity['dependency_isa']
    bootstrap=read(HOME_RELEASES/'sif-beta/runtimes/daily-20261026-40316174805-1-deepmd-kit/catalog.json')['memory_bootstrap']
    row=dict(identity=identity,image=str(image),sha256=delivery['image_sha256'],binds=binds,
             mpi_prefix=mpi,build_verified=True,scientific_verified=True,
             evidence=str(proof),evidence_sha256=digest(proof))
    plan.update(identity=identity,prefix=str(prefix),binary=str(binary),binary_sha256=digest(binary),
                image=str(image),image_sha256=delivery['image_sha256'],mpi_prefix=mpi,
                runtime_row=row,memory_bootstrap=bootstrap,probe_files=files)
    for name in ('controller','deployment/release-sif-efficiency'):
        source=REPO/name
        destination=root/'control'/name
        destination.mkdir(parents=True)
        for p in source.iterdir():
            if p.is_file() and p.suffix in ('.py','.json','.sh','.f90','.cmake'):
                shutil.copyfile(p,destination/p.name)
    plan['frozen_files']={str(p.relative_to(root)):digest(p) for d in ('inputs','control') for p in (root/d).rglob('*') if p.is_file()}
    write(root/'plan.json',plan)
    runtime=PROJECT/'runtime'/('rp-'+hashlib.sha256(str(root).encode()).hexdigest()[:12])
    runtime.mkdir(mode=0o700,parents=True)
    script='\n'.join([
        '#!/usr/bin/env bash', '#SBATCH --job-name=release-sif-'+software,
        '#SBATCH --nodes=1','#SBATCH --ntasks=1','#SBATCH --gpus-per-node=1',
        '#SBATCH --time='+('60' if software=='cp2k' else '25'), '#SBATCH --partition='+partition,
        '#SBATCH --qos='+('flood-1o2gpu' if partition=='16V100' else 'rush-1o2gpu'),
        '#SBATCH --account=proj-sai-hpc-software', '#SBATCH --chdir='+str(root),
        '#SBATCH --output='+str(root/'slurm-%j.log'),'#SBATCH --export=NIL',
        'set -eo pipefail','umask 077','export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
        'export HOME='+shlex.quote(str(Path.home())),
        'export USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?}',
        'export LD_LIBRARY_PATH="" LD_PRELOAD=""','source /etc/profile.d/lmod.sh','module purge',
        'module use /opt/modules/modulefiles/apps /opt/modules/modulefiles/devtools',
        'module load apptainer/1.4.4 '+plan['module'],
        'source /opt/sai_config/mps_mapping.d/'+partition+'.bash',
        'export OMP_NUM_THREADS='+('3' if partition=='8V100V0' else '4'),
        'export OMP_PLACES=cores OMP_PROC_BIND=close OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1',
        'export DP_INTRA_OP_PARALLELISM_THREADS=1 DP_INTER_OP_PARALLELISM_THREADS=1',
        'export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 SLURM_EXPORT_ENV=ALL',
        'export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher',
        'export TMPDIR='+shlex.quote(str(runtime)),
        'export PRTE_MCA_prte_tmpdir_base="$TMPDIR"',
        'export XDG_CACHE_HOME="$TMPDIR/cache" CUDA_CACHE_PATH="$TMPDIR/cuda"',
        'export TRITON_HOME="$TMPDIR/triton" APPTAINER_TMPDIR="$TMPDIR/apptainer" APPTAINER_CACHEDIR="$TMPDIR/apptainer-cache"',
        'mkdir -p "$XDG_CACHE_HOME" "$CUDA_CACHE_PATH" "$TRITON_HOME" "$APPTAINER_TMPDIR" "$APPTAINER_CACHEDIR"',
        'scontrol show job -o "$SLURM_JOB_ID" > allocation.txt',
        'module -t list > modules.txt 2>&1',
        '/usr/bin/python3 -u -B '+shlex.quote(str(root/'control/deployment/release-sif-efficiency/run.py'))+' run '+shlex.quote(str(root)),''
    ])
    (root/'job.sbatch').write_text(script)
    subprocess.run(['bash','-n',str(root/'job.sbatch')],check=True)
    command=['sbatch','--hold','--parsable']
    if partition=='8V100V0':command+=['--exclude=8v100v0n09']
    job=subprocess.check_output([*command,str(root/'job.sbatch')],text=True,cwd=root).strip().split(';')[0]
    assert job.isdigit()
    (root/'job.id').write_text(job+'\n')
    allocation=subprocess.check_output(['scontrol','show','job','-o',job],text=True)
    assert 'Account=proj-sai-hpc-software ' in allocation and 'WorkDir='+str(root) in allocation
    (root/'submission.txt').write_text(allocation)
    subprocess.run(['scontrol','release',job],check=True)
    print(json.dumps(dict(job=job,software=software,partition=partition,directory=str(root))),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('software',choices=('cp2k','abacus','lammps','deepmd-kit'))
    parser.add_argument('partition',choices=('16V100','4V100','8V100V0'))
    args=parser.parse_args()
    prepare(args.root,args.software,args.partition)
