#!/usr/bin/env python3
"""Check the trimmed native tree using an isolated mount view, without changing /opt.

Host MPI executes files installed into a project staging tree. The base SIF
supplies the mount namespace; application files come from the staging tree.
"""
import argparse
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import shutil
import shlex
import subprocess
import sys

sys.dont_write_bytecode = True
import install as deployment


def run(task):
    request = json.loads((task / 'request.json').read_text())
    stage, partition = Path(request['stage']), request['partition']
    deployment.require(os.environ['SLURM_JOB_PARTITION'] == partition, 'wrong Slurm partition')
    release = deployment.load_release()
    delivery, = [row for row in release['deliveries'] if row['partition'] == partition]
    entry = deployment.runtime_plan(release)[partition]
    prefix = delivery['identity']['install_prefix']
    deployment.verify_records(stage / prefix.lstrip('/'), {row['path']: row for row in entry['files']})
    deployment.require((stage / deployment.MODULE.relative_to('/')).read_bytes() ==
                       (deployment.PACKAGE / deployment.MODULE.name).read_bytes(), 'staged module differs')
    for name, expected in request['controls'].items():
        deployment.require(deployment.digest(deployment.PACKAGE / name) == expected, 'probe control changed')
    gpu = partition != 'DSPRHBM'
    science = deployment.pinned(deployment.package_file(delivery['evidence']['science']['path']),
                                delivery['evidence']['science']['sha256'])
    build_run = Path(delivery['origin_image']).stem
    original = deployment.ORIGIN / 'runtime-tests' / (build_run + '-lts-science-r' + ('4' if not gpu else '5'))
    for name in ('results', 'mpi', 'apptainer'):
        (task / name).mkdir(exist_ok=True)
    environment = dict(os.environ, OMP_NUM_THREADS='1', TMPDIR=str(task / 'mpi'),
                       APPTAINER_TMPDIR=str(task / 'apptainer'), APPTAINER_CACHEDIR=str(task / 'apptainer'))

    def execute(operation, cwd):
        args = ['mpirun', '-n', '1', '--map-by', 'ppr:1:node:pe=1', '--bind-to', 'core',
                'apptainer', 'exec', '--no-home', '--no-mount', 'bind-paths,home,cwd,tmp,hostfs', '--pwd', '/work']
        if gpu:
            args.append('--nv')
        for path in ('/usr', '/sbin', '/lib', '/lib64', '/opt/devtools', '/opt/modules', '/etc/lmod',
                     '/etc/profile.d/lmod.sh'):
            args += ['--bind', path + ':' + path + ':ro']
        for source, destination, mode in [
                (stage / 'opt/apps/abacus', '/opt/apps/abacus', 'ro'),
                (stage / 'opt/modules/modulefiles/apps', '/staged-modules', 'ro'),
                (deployment.PACKAGE, '/delivery', 'ro'), (cwd, '/work', 'rw'),
                (task / 'mpi', str(task / 'mpi'), 'rw')]:
            args += ['--bind', str(source) + ':' + destination + ':' + mode]
        args += [str(deployment.ORIGIN / 'containers/base/minimal-v1.sif'),
                 '/usr/bin/bash', '/delivery/staged-entry.sh', operation]
        return subprocess.run(args, cwd=cwd, env=environment, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=300)

    result = dict(partition=partition, identity=delivery['identity'], stage=str(stage),
                  job=os.environ['SLURM_JOB_ID'], release_sha256=deployment.digest(deployment.PACKAGE/'release.json'),
                  passed=False, cases=[])
    for operation in ('version', 'libraries'):
        process = execute(operation, task)
        (task / 'results' / (operation + '.log')).write_text(process.stdout)
        deployment.require(process.returncode == 0, operation + ' failed')
        if operation == 'version':
            deployment.require('ABACUS version v3.10.1' in process.stdout, 'wrong version')
        else:
            deployment.require('not found' not in process.stdout, 'missing native library')
            for name in ('libmpi', 'libelpa', 'libxc', 'libtorch_cpu'):
                deployment.require(re.search(name + r'.*=> /', process.stdout), 'missing ' + name)
            if gpu:
                for name in ('libcal', 'libcusolverMp'):
                    deployment.require(re.search(name + r'.*=> ' + re.escape(deployment.MATH), process.stdout),
                                       'incorrect LTS GPU math library')
    for reference in science['results']:
        name, case = reference['case'], task / reference['case']
        case.mkdir()
        for filename in ('INPUT', 'STRU', 'KPT', 'model.ptg', 'PP_ORB'):
            source = original / name / filename
            if source.is_dir():
                shutil.copytree(source, case / filename)
            elif source.is_file():
                shutil.copyfile(source, case / filename)
        process = execute('science', case)
        (task / 'results' / (name + '.log')).write_text(process.stdout)
        text = (case / 'OUT.autotest/running_scf.log').read_text() if process.returncode == 0 else ''
        values = re.findall(r'!FINAL_ETOT_IS\s+([-+0-9.eE]+)', text)
        value = float(values[-1]) if values else None
        passed = (process.returncode == 0 and value is not None and math.isfinite(value)
                  and abs(value-reference['reference_energy_ev']) <= reference['energy_tolerance_ev']
                  and 'charge density convergence is achieved' in text.lower())
        if reference['device'] == 'gpu':
            passed = passed and bool(re.search(r'^\s*RUNNING WITH DEVICE\s*:\s*GPU\s*/\s*\S+', text, re.M))
        if name == 'cusolvermp':
            passed = passed and bool(re.search(r'^\s*DiagoCusolverMP\s+Diag_CusolverMP_gvd\s', text, re.M))
        result['cases'].append(dict(case=name, passed=passed, energy_ev=value,
                                   reference_ev=reference['reference_energy_ev']))
        (task / 'results/verification.json').write_text(json.dumps(result, indent=2) + '\n')
    result['passed'] = all(row['passed'] for row in result['cases'])
    (task / 'results/verification.json').write_text(json.dumps(result, indent=2) + '\n')
    deployment.require(result['passed'], 'staged native calculation failed')
    print(json.dumps(result), flush=True)


def submit(stage):
    stage = stage.resolve(strict=True)
    deployment.require(stage.is_relative_to(deployment.ORIGIN / 'operations'), 'stage must be in project operations')
    deployment.preflight(deployment.load_release(), stage, installed=True)
    root = stage.parent / ('checks-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    root.mkdir()
    for partition in deployment.PARTITIONS:
        task = root / partition
        task.mkdir()
        for name in ('mpi', 'apptainer'):
            (task / name).mkdir()
        request = dict(stage=str(stage), partition=partition, controls={name: deployment.digest(deployment.PACKAGE/name)
                       for name in ('install.py', 'validate-staged.py', 'staged-entry.sh', deployment.MODULE.name,
                                    'release.json', 'runtime-plan.json')})
        (task / 'request.json').write_text(json.dumps(request, indent=2)+'\n')
        cpu = partition == 'DSPRHBM'
        qos = 'rush-cpu' if cpu else 'rush-1o2gpu' if partition in ('4V100', '8V100V0') else 'flood-1o2gpu'
        lines = ['#!/bin/bash', '#SBATCH --job-name=abacus-lts-native-probe',
                 '#SBATCH --partition=' + partition, '#SBATCH --qos=' + qos,
                 '#SBATCH --account=proj-sai-hpc-software', '#SBATCH --nodes=1', '#SBATCH --ntasks=1',
                 '#SBATCH --time=15', '#SBATCH --export=NIL', '#SBATCH --chdir=' + str(task),
                 '#SBATCH --output=' + str(task / 'slurm-%j.log')]
        lines += ['#SBATCH --cpus-per-task=1', '#SBATCH --mem=6G', '#SBATCH --exclude=d9470n07'] if cpu else ['#SBATCH --gpus-per-node=1']
        lines += ['set -eo pipefail', 'export HOME=' + shlex.quote(str(Path.home())),
                  'export USER=$SLURM_JOB_USER LOGNAME=$SLURM_JOB_USER',
                  'export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
                  'export LD_LIBRARY_PATH="" LD_PRELOAD=""', 'source /etc/profile.d/lmod.sh', 'module purge',
                  'module load apptainer/1.4.4 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto',
                  'export SLURM_EXPORT_ENV=ALL OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher',
                  shlex.join(['python3', '-B', str(deployment.PACKAGE/'validate-staged.py'), '--run', str(task)])]
        script = task/'job.sbatch'
        script.write_text('\n'.join(lines)+'\n')
        subprocess.run(['bash', '-n', str(script)], check=True)
        job = subprocess.check_output(['sbatch', '--parsable', str(script)], text=True).strip().split(';')[0]
        deployment.require(job.isdigit(), 'invalid Slurm job')
        (task/'job.id').write_text(job+'\n')
        print(json.dumps(dict(partition=partition, job=job, task=str(task))), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--submit', type=Path)
    action.add_argument('--run', type=Path)
    args = parser.parse_args()
    if args.submit:
        submit(args.submit)
    else:
        run(args.run)
