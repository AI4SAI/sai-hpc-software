#!/usr/bin/env python3
"""Observe JAX's actual device with GPUs visible; diagnostic, not acceptance."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

PROJECT = Path('/home/stardust/sai-hpc-software')
ROOT = PROJECT / 'experimental/deepmd-lammps'
CONTROL = ROOT / 'controller/acceptance/21a8a3cc5d6b35feb322425d4d6be8be85134a63/lammps-release-20261020'
BUILD = 'md-39403084059-1-2026-10-20-5fef92e054bacdaf-export'


def main():
    os.umask(0o077)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(CONTROL))
    from source_cache import checksum
    from md_tracking import fingerprint
    from md_acceptance_recovery import capture
    from md_publication import build_record
    delivery = json.loads((ROOT / 'runs' / BUILD / 'request.json').read_text())['delivery']
    binding = capture(ROOT, CONTROL, BUILD, delivery, 'Read-only JAX execution-device diagnostic')
    _, image, digest = build_record(BUILD, controller_recovery=binding)
    task = ROOT / 'runtime-tests/md-jax-device-20261020-r1'
    task.mkdir()
    for part in ('results', 'runtime', 'baseline', 'candidate'):
        (task / part).mkdir()
    failed = ROOT / 'runtime-tests' / (BUILD + '-performance') / 'results/jax/calibration-00'
    for side in ('baseline', 'candidate'):
        for name in ('data.lmp', 'plumed.dat', 'in.jax', 'in.performance', 'model.savedmodel'):
            source, target = failed / name, task / side / name
            shutil.copytree(source, target) if source.is_dir() else shutil.copyfile(source, target)
    nsys = (ROOT / 'runtime-tests' / (BUILD + '-performance') / 'results/nsys.path').read_text().strip()
    lines = ['#!/usr/bin/env bash', '#SBATCH --export=NIL', '#SBATCH --partition=16V100',
             '#SBATCH --qos=flood-1o2gpu', '#SBATCH --gpus-per-node=1', '#SBATCH --nodes=1',
             '#SBATCH --ntasks=1', '#SBATCH --time=10', '#SBATCH --job-name=md-jax-device',
             '#SBATCH --output=' + str(task / 'results/slurm-%j.log'), 'set -euo pipefail',
             'export HOME=/home/stardust USER=stardust LOGNAME=stardust PATH=/usr/bin:/bin',
             'export LD_LIBRARY_PATH="" LD_PRELOAD=""',
             'export SAI_SOFTWARE_ROOT=' + shlex.quote(str(PROJECT)),
             'export SAI_MD_DELIVERY=' + shlex.quote(json.dumps(delivery, sort_keys=True)),
             'export SAI_MD_IMAGE=' + shlex.quote(str(image)),
             'export SAI_MD_TEST_CONTROL=' + shlex.quote(str(CONTROL)),
             'export SAI_MD_PROFILE_LMP=' + shlex.quote(nsys),
             'export TMPDIR=' + shlex.quote(str(task / 'runtime')),
             'export APPTAINER_TMPDIR="$TMPDIR" APPTAINER_CACHEDIR="$TMPDIR/cache"',
             'export OMP_NUM_THREADS=1 DP_INTRA_OP_PARALLELISM_THREADS=1 DP_INTER_OP_PARALLELISM_THREADS=1',
             'mkdir -p "$APPTAINER_CACHEDIR"', 'source /etc/profile.d/lmod.sh',
             'module use /opt/modules/modulefiles/devtools',
             'module load apptainer/1.4.4 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto']
    for side in ('baseline', 'candidate'):
        lines += ['cd ' + shlex.quote(str(task / side)), 'export SAI_MD_IMPLEMENTATION=' + side,
                  shlex.join(['bash', str(CONTROL / 'md_runtime.sh'), 'lmp', '-in', 'in.performance',
                              '-log', 'log.lammps']) + ' >stdout.txt 2>&1',
                  shlex.join([nsys, 'stats', '--report', 'cuda_gpu_trace,cuda_api_sum', '--format', 'csv',
                              '--timeunit', 'ns', 'cuda-trace.nsys-rep']) + ' >cuda-stats.csv 2>cuda-stats.stderr.txt']
    script = task / 'job.sbatch'
    script.write_text('\n'.join(lines) + '\n')
    subprocess.run(['bash', '-n', str(script)], check=True)
    record = dict(diagnostic_only=True, artifact=str(image), artifact_sha256=digest,
                  controller=str(CONTROL), recipe_sha256=fingerprint(CONTROL),
                  script_sha256=checksum(script), helper_sha256=checksum(Path(__file__)))
    (task / 'request.json').write_text(json.dumps(record, sort_keys=True) + '\n')
    env = dict(PATH='/usr/bin:/bin', HOME=str(Path.home()), USER='stardust', LOGNAME='stardust', SBATCH_EXPORT='ALL')
    job = subprocess.check_output(['sbatch', '--hold', '--parsable', str(script)], env=env, text=True).strip()
    assert job.isdigit()
    (task / 'job.id').write_text(job + '\n')
    subprocess.run(['scontrol', 'release', job], check=True)
    print(json.dumps(dict(job=job, task=str(task), diagnostic_only=True)))


if __name__ == '__main__':
    main()
