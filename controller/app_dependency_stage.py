#!/usr/bin/env python3
"""Prepare final-prefix application dependencies in a new, isolated container."""
import argparse
import json
from pathlib import Path
import shlex
import shutil
import subprocess

from cp2k_dependency_stage import paths, accounting
from cp2k_image_driver import wrap_command, validate_receipt, module_load_command
from devtools_install import checksum, require
from qe_dependencies import HDF5_SHA256
from remote_controller import (container_command, cp2k_mpi_build_binds,
    project_batch_directives, project_qos, private_scratch, safe_name)

PURPOSE = 'qe-hdf5-abacus-libtorch-preinstallation'
ABACUS_SOURCE = '27c3e1065bfe0ca6be2d9b90a93da91bfea1ffe0'
MODEL = 'tests/09_DeePKS/Model_ProjOrb/model_lda_pbe_18.ptg'


def files(control):
    return {p.name: checksum(p) for p in sorted(control.iterdir()) if p.is_file()}


def render(project, control, run, hdf5_input):
    project, control, run = paths(project, control, run)
    image = project / 'containers/base/minimal-v1.sif'
    overlay = run / 'work.ext3'
    sif = run / 'libraries.sif'
    torch_archives = '/opt/apps/abacus/abacus-develop-3.9.0.26_nvhpc263gnu/toolchain/build'
    def container(phase):
        final = phase == 'verify'
        binds = (*cp2k_mpi_build_binds(), (run/'input/deepks.ptg', '/input/deepks.ptg'))
        if not final:
            binds += ((hdf5_input, '/input/qe-dependencies'), (torch_archives, '/input/abacus-dependencies'))
        argv = container_command(sif if final else image,
            ['/usr/bin/bash', '/control/app_dependency_entry.sh', phase],
            overlay=None if final else overlay, control=control, jobs=8,
            extra_binds=binds, runtime=run/'runtime' if final else None)
        argv[2:2] = ['--ignore-fakeroot-command', '--bind', str(run/'results')+':/results:rw',
                     '--env', 'SLURM_JOB_PARTITION=DSPRHBM']
        if final:
            argv[2:2] = ['--bind', str(run/'runtime')+':/root/.config:rw']
        return argv if final else wrap_command(project, run, control, argv)
    emit = container_command(image, ['/usr/bin/cat', '/workspace/final.squashfs'],
                             overlay=str(overlay)+':ro', jobs=8)
    emit.insert(2, '--ignore-fakeroot-command')
    emit = wrap_command(project, run, control, emit)
    q = shlex.quote
    lines = ['#!/bin/bash', '#SBATCH --job-name=sai-app-dependencies',
        '#SBATCH --partition=DSPRHBM', '#SBATCH --qos='+project_qos(project, 'dsprhbm'),
        '#SBATCH --nodes=1', '#SBATCH --ntasks=1', '#SBATCH --cpus-per-task=8',
        '#SBATCH --mem=28G', '#SBATCH --time=30', '#SBATCH --export=NIL',
        '#SBATCH --output='+str(run/'results/slurm-%j.log'),
        *project_batch_directives(project, run),
        'set -eo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""',
        'source /etc/profile.d/lmod.sh', module_load_command(), 'set -u', 'umask 077',
        'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
        'export TMPDIR='+q(str(run/'runtime')), 'export APPTAINER_TMPDIR="$TMPDIR"',
        'export APPTAINER_CACHEDIR='+q(str(run/'apptainer-cache')),
        'test ! -e '+q(str(overlay)),
        shlex.join(['apptainer','overlay','create','--fakeroot','--sparse','--size','8192',str(overlay)]),
        shlex.join(container('build')),
        shlex.join(['/usr/sbin/e2fsck','-fn',str(overlay)])+' > '+q(str(run/'results/fsck.log'))+' 2>&1',
        shlex.join(emit)+' > '+q(str(run/'libraries.squashfs')),
        shlex.join(['apptainer','sif','new',str(sif)]),
        shlex.join(['apptainer','sif','add','--datatype','4','--partfs','1','--parttype','2',
                    '--partarch','2','--groupid','1',str(sif),str(run/'libraries.squashfs')]),
        shlex.join(container('verify')),
        shlex.join(['sha256sum',str(sif)])+' > '+q(str(run/'results/artifact.sha256')),
        shlex.join(['chmod','444',str(sif)]),
        'echo APPLICATION_DEPENDENCY_CONSUMERS_PASSED']
    return '\n'.join(lines)+'\n'


def submit(project, control, run_id, hdf5_input):
    project, control, run = paths(project, control, Path(project)/'runs'/safe_name(run_id))
    require(not run.exists(), 'Run already exists; inspect it instead of resubmitting')
    hdf5_input = Path(hdf5_input).resolve(strict=True)
    require(hdf5_input.is_relative_to(project), 'HDF5 input must stay in project storage')
    require(checksum(hdf5_input/'hdf5-1.14.6.tar.gz') == HDF5_SHA256, 'HDF5 source changed')
    run.mkdir(mode=0o700)
    for name in ('results', 'input'):
        (run/name).mkdir(mode=0o700)
    for name in ('runtime', 'apptainer-cache'):
        private_scratch(run/name)
    with (run/'input/deepks.ptg').open('xb') as model:
        subprocess.run(['git','-C',str(project/'cache/repositories/abacus'),
                        'show', ABACUS_SOURCE+':'+MODEL], stdout=model, check=True)
    script = run/'job.sbatch'
    script.write_text(render(project, control, run, hdf5_input))
    subprocess.run(['bash','-n',str(script)], check=True)
    request = dict(schema=1, purpose=PURPOSE, controller=str(control),
        controller_files=files(control), script_sha256=checksum(script), hdf5_input=str(hdf5_input),
        model=dict(source_sha=ABACUS_SOURCE, path=MODEL, sha256=checksum(run/'input/deepks.ptg')))
    (run/'request.json').write_text(json.dumps(request, sort_keys=True)+'\n')
    result = subprocess.check_output(['sbatch','--hold','--parsable',str(script)], cwd=run, text=True)
    job = result.strip().split(';')[0]
    require(job.isdigit(), 'Unexpected scheduler response')
    (run/'job.id').write_text(job+'\n')
    request['job_id'] = job
    (run/'request.json').write_text(json.dumps(request, sort_keys=True)+'\n')
    subprocess.run(['scontrol','release',job], check=True)
    print(job, flush=True)


def submitted(project, run_id):
    run = Path(project)/'runs'/safe_name(run_id)
    request = json.loads((run/'request.json').read_text())
    project, control, run = paths(project, request['controller'], run)
    require(request['purpose'] == PURPOSE and request['controller_files'] == files(control),
            'Frozen controller changed')
    require(checksum(run/'job.sbatch') == request['script_sha256'], 'Submitted script changed')
    return project, control, run, request


def build_evidence(project, run):
    results = run/'results'
    validate_receipt(project, json.loads((results/'image-driver.json').read_text()))
    require(json.loads((results/'overlay-sync-dependencies.json').read_text())['synchronized'],
            'Missing filesystem synchronization')
    require((results/'fsck.log').is_file(), 'Missing filesystem check')
    lifecycle = list(results.glob('managed-overlay-*.json'))
    require(len(lifecycle) == 2, 'Expected managed build and read-only export receipts')
    for p in lifecycle:
        record = json.loads(p.read_text())
        require(record.get('accepted') and record.get('completed') and not record.get('forced_stop')
                and record.get('payload_returncode') == 0 and record.get('driver_returncode') == 0,
                'Container teardown failed')


def reverify(project, control, source_id, run_id):
    project, _, source, original = submitted(project, source_id)
    require('source_build' not in original, 'Reverify the original build')
    _, state = accounting(source)
    require(state and state[0] in ('FAILED','COMPLETED'), 'Original build must be terminal')
    build_evidence(project, source)
    project, control, run = paths(project, control, project/'runs'/safe_name(run_id))
    require(not run.exists(), 'Verification run already exists')
    run.mkdir(mode=0o700)
    shutil.copytree(source/'results',run/'results')
    # Preserve failed receipts in the original run; new consumer outputs get new names/paths.
    for p in (run/'results').glob('*-passed.json'): p.unlink()
    shutil.copytree(source/'input',run/'input')
    for name in ('runtime','apptainer-cache'): private_scratch(run/name)
    image_sha = checksum(source/'libraries.sif')
    shutil.copyfile(source/'libraries.sif',run/'libraries.sif')
    require(checksum(run/'libraries.sif') == image_sha, 'Verification SIF copy changed')
    (run/'libraries.sif').chmod(0o444)
    source_evidence = {str(p.relative_to(source)):checksum(p) for p in
        [source/'request.json',source/'job.sbatch',*sorted((source/'results').rglob('*'))]
        if p.is_file()}
    script = render(project,control,run,Path(original['hdf5_input']))
    lines=script.splitlines()
    setup=lines[:next(i for i,line in enumerate(lines) if line.startswith('test ! -e '))]
    verification=next(line for line in lines if '/control/app_dependency_entry.sh verify' in line)
    setup += [verification, shlex.join(['sha256sum',str(run/'libraries.sif')])+' > '+
              shlex.quote(str(run/'results/artifact.sha256')), 'echo APPLICATION_DEPENDENCY_CONSUMERS_PASSED']
    script=run/'job.sbatch';script.write_text('\n'.join(setup)+'\n')
    subprocess.run(['bash','-n',str(script)],check=True)
    request=dict(schema=1,purpose=PURPOSE,controller=str(control),controller_files=files(control),
        script_sha256=checksum(script),source_build=dict(run_id=source_id,sif_sha256=image_sha,
        evidence_sha256=source_evidence),hdf5_input=original['hdf5_input'],model=original['model'])
    (run/'request.json').write_text(json.dumps(request,sort_keys=True)+'\n')
    job=subprocess.check_output(['sbatch','--hold','--parsable',str(script)],cwd=run,text=True).strip().split(';')[0]
    require(job.isdigit(),'Unexpected scheduler response')
    (run/'job.id').write_text(job+'\n');request['job_id']=job
    (run/'request.json').write_text(json.dumps(request,sort_keys=True)+'\n')
    subprocess.run(['scontrol','release',job],check=True)
    print(job,flush=True)


def accepted(project, run_id):
    project, control, run, request = submitted(project, run_id)
    job, state = accounting(run)
    require(job == request['job_id'] and state == ['COMPLETED','0:0'], 'Build is not completed successfully')
    results = run/'results'
    build_evidence(project,run)
    if 'source_build' in request:
        original=request['source_build'];source=project/'runs'/safe_name(original['run_id'])
        for name, expected in original['evidence_sha256'].items():
            require(checksum(source/name)==expected,'Original build evidence changed')
        require(checksum(run/'libraries.sif')==original['sif_sha256'], 'Original SIF identity changed')
    provider = results/'provider.json'
    for name in ('hdf5', 'torch'):
        receipt = json.loads((results/(name+'-passed.json')).read_text())
        require(receipt['passed'] and receipt['provider_sha256'] == checksum(provider),
                'Consumer acceptance does not match provider')
    require(checksum(run/'libraries.sif') == (results/'artifact.sha256').read_text().split()[0],
            'Accepted library SIF changed')
    return json.loads(provider.read_text())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='op', required=True)
    p = sub.add_parser('submit')
    p.add_argument('project',type=Path); p.add_argument('controller',type=Path)
    p.add_argument('run_id'); p.add_argument('--hdf5-input',type=Path,required=True)
    p = sub.add_parser('check'); p.add_argument('project',type=Path); p.add_argument('run_id')
    p = sub.add_parser('reverify'); p.add_argument('project',type=Path); p.add_argument('controller',type=Path)
    p.add_argument('source_id'); p.add_argument('run_id')
    args = parser.parse_args()
    if args.op == 'submit':
        submit(args.project,args.controller,args.run_id,args.hdf5_input)
    elif args.op == 'reverify':
        reverify(args.project,args.controller,args.source_id,args.run_id)
    else:
        print(json.dumps(accepted(args.project,args.run_id),sort_keys=True))
