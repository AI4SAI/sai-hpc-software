#!/usr/bin/env python3
"""Exercise the public container selector and two CUDA paths on each partition."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

PROJECT = Path('/home/stardust/sai-hpc-software')
ROOT = PROJECT / 'experimental/deepmd-lammps'
PACKAGE = PROJECT / 'deployment/lammps-30Sep2026'
MODULE = PACKAGE / 'modulefiles/lammps/30Sep2026-deepmd320-gpu'
GPU_HELPER = PROJECT / 'diagnostics/lammps-release-20261020/gpu_functional_smoke-matched-precision.py'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, value):
    with path.open('x') as output:
        json.dump(value, output, sort_keys=True)
        output.write('\n')


def submit():
    release = json.loads((PACKAGE / 'release.json').read_text())
    for delivery in release['deliveries']:
        target, build = delivery['partition'], delivery['build_run']
        task = ROOT / 'runtime-tests' / (build + '-published-module')
        task.mkdir()
        (task / 'results').mkdir()
        (task / 'runtime').mkdir()
        helper = task / Path(__file__).name
        shutil.copyfile(__file__, helper)
        helper.chmod(0o444)
        request = dict(delivery=delivery, helper_sha256=sha(helper), module_sha256=sha(MODULE),
                       comparator_sha256=sha(GPU_HELPER))
        write(task / 'request.json', request)
        script = task / 'job.sbatch'
        script.write_text('\n'.join([
            '#!/usr/bin/env bash', '#SBATCH --export=NIL', '#SBATCH --partition=' + target,
            '#SBATCH --qos=' + ('flood-1o2gpu' if target == '16V100' else 'rush-1o2gpu'),
            '#SBATCH --gpus-per-node=1', '#SBATCH --nodes=1', '#SBATCH --ntasks=1', '#SBATCH --time=15',
            '#SBATCH --job-name=md-published-module-' + target,
            '#SBATCH --output=' + str(task / 'results/slurm-%j.log'), 'set -euo pipefail',
            'export HOME=/home/stardust USER=stardust LOGNAME=stardust PATH=/usr/bin:/bin',
            'export LD_LIBRARY_PATH="" LD_PRELOAD="" PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1',
            'export DP_INTRA_OP_PARALLELISM_THREADS=1 DP_INTER_OP_PARALLELISM_THREADS=1',
            'export TMPDIR=' + shlex.quote(str(task / 'runtime')),
            'export APPTAINER_TMPDIR="$TMPDIR" APPTAINER_CACHEDIR="$TMPDIR/cache"',
            'mkdir -p "$APPTAINER_CACHEDIR"', 'source /etc/profile.d/lmod.sh',
            'module use ' + str(PACKAGE / 'modulefiles'),
            'module load lammps/30Sep2026-deepmd320-gpu',
            shlex.join(['/usr/bin/python3', '-u', str(helper), 'run', str(task)]),
            'module unload lammps/30Sep2026-deepmd320-gpu',
            'test -z "${SAI_LAMMPS_RELEASE_PARTITION-}"',
            'test -z "${SAI_LAMMPS_IMAGE-}"',
            'touch ' + shlex.quote(str(task / 'results/module-unloaded')), '']))
        subprocess.run(['bash', '-n', str(script)], check=True)
        env = dict(PATH='/usr/bin:/bin', HOME='/home/stardust', USER='stardust', LOGNAME='stardust', SBATCH_EXPORT='ALL')
        job = subprocess.check_output(['sbatch', '--hold', '--parsable', str(script)], text=True, env=env).strip()
        if not job.isdigit():
            raise ValueError('invalid job handle')
        (task / 'job.id').write_text(job + '\n')
        subprocess.run(['scontrol', 'release', job], check=True)
        print(json.dumps(dict(partition=target, job=job, task=str(task))), flush=True)


def run(task):
    import importlib.util
    request = json.loads((task / 'request.json').read_text())
    delivery = request['delivery']
    if (sha(MODULE) != request['module_sha256'] or sha(Path(__file__)) != request['helper_sha256']
            or sha(GPU_HELPER) != request['comparator_sha256']):
        raise ValueError('frozen module/helper changed')
    if (os.environ['SLURM_JOB_PARTITION'] != delivery['partition']
            or os.environ['SAI_LAMMPS_RELEASE_PARTITION'] != delivery['partition']
            or os.environ['SAI_LAMMPS_IMAGE'] != str(PROJECT / delivery['image'])):
        raise ValueError('module selected another partition/image')
    launcher = shutil.which('lmp')
    if launcher not in delivery['modules']['lammps']['launchers']:
        raise ValueError('module selected another launcher')
    module = importlib.util.spec_from_file_location('gpu_comparator', GPU_HELPER)
    comparator = importlib.util.module_from_spec(module)
    module.loader.exec_module(comparator)
    with (task / 'results/help.txt').open('x') as output:
        subprocess.run(['lmp', '-h'], check=True, stdout=output, stderr=subprocess.STDOUT, timeout=180)
    results = {}
    for name, options in (
        ('lj-candidate-gpu', ['-pk', 'gpu', '1', 'split', '1.0']),
        ('coulomb-pppm-candidate-kokkos', ['-k', 'on', 'g', '1', 't', '1', '-sf', 'kk', '-pk', 'kokkos', 'gpu/aware', 'off']),
    ):
        source = ROOT / 'runtime-tests' / (delivery['build_run'] + '-gpu-functional') / name
        case = task / name
        case.mkdir()
        for filename in ('in.lmp', 'data.lmp'):
            shutil.copyfile(source / filename, case / filename)
        with (case / 'stdout.txt').open('x') as output:
            subprocess.run(['lmp', *options, '-in', 'in.lmp', '-log', 'log.lammps'], cwd=case,
                           check=True, stdout=output, stderr=subprocess.STDOUT, timeout=300)
        observed, expected = comparator.values(case), comparator.values(source)
        for actual, reference in zip(observed, expected):
            if any(abs(a-b) > 2e-6 + 2e-5 * abs(b) for a, b in zip(actual, reference)):
                raise ValueError('published module numerical result changed: ' + name)
        results[name] = dict(passed=True, max_absolute_error=max(abs(a-b) for actual, reference in
                              zip(observed, expected) for a,b in zip(actual, reference)),
                             stdout_sha256=sha(case / 'stdout.txt'), dump_sha256=sha(case / 'result.dump'))
    write(task / 'results/evidence.json', dict(passed=True, partition=delivery['partition'], job=os.environ['SLURM_JOB_ID'],
          node=os.uname().nodename, launcher=launcher, image=os.environ['SAI_LAMMPS_IMAGE'],
          module_sha256=request['module_sha256'], cases=results))


if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('submit', 'run'))
    parser.add_argument('task', nargs='?', type=Path)
    args = parser.parse_args()
    if args.operation == 'submit':
        submit()
    else:
        run(args.task)
