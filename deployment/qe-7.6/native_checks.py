#!/usr/bin/env python3
"""Submit native QE checks after installation, or against a private staged copy."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import install as deployment

PROJECT = Path('/org/org-open-sai/proj-sai-hpc-software')


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True); stream.write('\n')


def submit(stage_root):
    release = deployment.load_release()
    deployment.preflight(release, deployment.PACKAGE, stage_root, installed=True)
    root = PROJECT / 'operations' / ('qe-native-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    root.mkdir()
    control = root / 'control'
    control.mkdir()
    for name in ('install.py', 'native_checks.py', 'release.json', '7.6-auto'):
        shutil.copyfile(deployment.PACKAGE / name, control / name)
    module_root = root / 'modulefiles'
    (module_root / 'qe').mkdir(parents=True)
    text = (deployment.PACKAGE / deployment.MODULE.name).read_text()
    if stage_root != Path('/'):
        text = text.replace('set prefix /opt/apps/qe/', 'set prefix ' + str(stage_root / 'opt/apps/qe') + '/')
    module = module_root / 'qe/7.6-auto'
    module.write_text(text)
    jobs = []
    for row in release['deliveries']:
        partition = row['partition']
        output = root / partition
        output.mkdir(); (output / 'tmp').mkdir(mode=0o700)
        publication = deployment.pinned(deployment.PACKAGE / row['publication'], row['publication_sha256'])
        original = Path(publication['controller'])
        sys.path.insert(0, str(original))
        from remote_controller import project_qos
        prefix = stage_root / Path(row['prefix']).relative_to('/')
        request = dict(partition=partition, prefix=str(prefix), source_image_sha256=row['image_sha256'],
                       controller=str(original), checker_sha256=deployment.digest(original / 'qe_science.py'),
                       fixture_sha256=deployment.digest(original / 'qe_fixture.json'),
                       module_sha256=deployment.digest(module), stage_root=str(stage_root))
        save(output / 'request.json', request)
        wrapper = output / 'rank.sh'
        wrapper.write_text('#!/usr/bin/env bash\nset -eu\n'
                           'program=$1; shift\n'
                           'printf "%s\\t%s\\t%s\\t%s\\n" "$OMPI_COMM_WORLD_RANK" "$MPI_HOME" "$QE_ROOT" "$program" > "$SAI_QE_TRACE_DIR/rank-$OMPI_COMM_WORLD_RANK.tsv"\n'
                           'exec "$QE_ROOT/bin/$program" "$@"\n')
        wrapper.chmod(0o555)
        gpu = partition != 'DSPRHBM'
        q = shlex.quote
        lines = ['#!/usr/bin/env bash', '#SBATCH --job-name=qe-native-' + partition,
                 '#SBATCH --account=proj-sai-hpc-software', '#SBATCH --partition=' + partition,
                 '#SBATCH --qos=' + project_qos(PROJECT, row['identity']['target']),
                 '#SBATCH --nodes=1', '#SBATCH --ntasks=2', '#SBATCH --time=5', '#SBATCH --export=NIL',
                 '#SBATCH --chdir=' + str(output), '#SBATCH --output=' + str(output / 'slurm-%j.log'),
                 *(['#SBATCH --gpus-per-node=2'] if gpu else ['#SBATCH --cpus-per-task=2', '#SBATCH --mem=8G']),
                 'set -eo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH= LD_PRELOAD=',
                 'export HOME=' + q(str(Path.home())), 'export USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?}',
                 'source /etc/profile.d/lmod.sh', 'module purge', 'module use ' + q(str(module_root)),
                 'module load qe/7.6-auto', 'export TMPDIR=' + q(str(output / 'tmp')),
                 'export SLURM_EXPORT_ENV=ALL OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher',
                 'export OMPI_MCA_shmem_mmap_backing_file_base_dir=/dev/shm OMPI_MCA_shmem_mmap_relocate_backing_file=1',
                 'export UCX_WARN_UNUSED_ENV_VARS=n',
                 'python3 -B ' + q(str(control / 'native_checks.py')) + ' run ' + q(str(output))]
        script = output / 'job.sbatch'
        script.write_text('\n'.join(lines) + '\n')
        subprocess.run(['bash', '-n', script], check=True)
        job = subprocess.check_output(['sbatch', '--parsable', script], text=True).strip().split(';')[0]
        deployment.require(job.isdigit(), 'invalid native job ID')
        (output / 'job.id').write_text(job + '\n')
        jobs.append(dict(partition=partition, job=job, output=str(output), script_sha256=deployment.digest(script)))
        print(partition + ': ' + job, flush=True)
    save(root / 'jobs.json', jobs)
    print(root, flush=True)


def run(output):
    request = json.loads((output / 'request.json').read_text())
    prefix, control = Path(request['prefix']), Path(request['controller'])
    deployment.require(os.environ['SLURM_JOB_PARTITION'] == request['partition']
                       and os.environ['QE_ROOT'] == str(prefix) and shutil.which('pw.x') == str(prefix / 'bin/pw.x'),
                       'wrong native module/partition')
    deployment.require(deployment.digest(control / 'qe_science.py') == request['checker_sha256']
                       and deployment.digest(control / 'qe_fixture.json') == request['fixture_sha256'], 'science checker changed')
    release = deployment.load_release()
    row, = [item for item in release['deliveries'] if item['partition'] == request['partition']]
    deployment.verify_installed(prefix, row['files'])
    links = {}
    for binary in sorted((prefix / 'bin').iterdir()):
        text = subprocess.check_output(['ldd', binary], text=True)
        deployment.require('not found' not in text, 'missing native dependency: ' + binary.name)
        links[binary.name] = text
    save(output / 'ldd.json', links)
    sys.path.insert(0, str(control))
    import qe_science
    science = qe_science.run(output / 'science', output / 'rank.sh', request['partition'] != 'DSPRHBM')
    report = dict(passed=True, job=os.environ['SLURM_JOB_ID'], node=os.uname().nodename,
                  request=request, science=science, commands_checked=len(links),
                  mpi_home=os.environ['MPI_HOME'], module_sha256=request['module_sha256'])
    save(output / 'native-check.json', report)
    print('QE_NATIVE_SCF_PH_PP_ACCEPTED', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    p = sub.add_parser('submit')
    p.add_argument('--stage-root', type=Path, default=Path('/'))
    p = sub.add_parser('run')
    p.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.operation == 'submit':
        submit(args.stage_root.resolve(strict=True))
    else:
        run(args.output)
