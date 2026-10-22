#!/usr/bin/env python3
"""Freeze and submit a CPU companion build using sealed read-only dependencies."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path('/home/stardust/sai-hpc-software')
REPO = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--partition', choices=('4V100', '16V100'), required=True)
parser.add_argument('--name', required=True)
parser.add_argument('--gpus', type=int, default=1, help='Build-only allocation; four physical cores per GPU')
args = parser.parse_args()
assert re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,80}', args.name)
assert 1 <= args.gpus <= (4 if args.partition == '4V100' else 16)
run = ROOT/'runs'/('cp2k-water-cpu-'+args.name)
run.mkdir()
for name in ('work', 'install', 'input', 'control', 'results', 'runtime', 'cache'):
    (run/name).mkdir()
for name in ('controller', 'deployment/cp2k-water-scaling'):
    shutil.copytree(REPO/name, run/'control'/name, ignore=shutil.ignore_patterns('__pycache__'))
prefix = Path('/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43')/args.partition
shutil.copyfile(prefix/'share/sai/spack-dependencies.json', run/'input/dependencies.json')
inputs = {str(p.relative_to(run)): hashlib.sha256(p.read_bytes()).hexdigest()
          for folder in ('control', 'input') for p in (run/folder).rglob('*') if p.is_file()}
(run/'request.json').write_text(json.dumps(dict(partition=args.partition, source_sha='67b5da876dd6a76b8b021d5a04d1c81ba79a4c50',
    prefix=str(run/'install'), reused_dependency_prefix=str(prefix), inputs=inputs), indent=2)+'\n')
qos = ('rush-1o2gpu' if args.partition == '4V100' else 'flood-1o2gpu') if args.gpus == 1 else 'rush-gpu'
job = subprocess.check_output(['sbatch', '--parsable', '--partition='+args.partition, '--qos='+qos,
    '--gpus-per-node='+str(args.gpus), '--time=240',
    '--job-name=cp2k-water-cpu-'+args.name, '--output='+str(run/'results/slurm-%j.log'),
    str(run/'control/deployment/cp2k-water-scaling/build-cpu.sbatch'), str(run)], text=True).strip()
assert job.isdigit(), job
(run/'job.id').write_text(job+'\n')
print(json.dumps(dict(job=job, run=str(run), partition=args.partition, physical_cores=4*args.gpus)))
