#!/usr/bin/env python3
"""Freeze and submit the isolated, one-object CPU repair in its original container."""
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
args = parser.parse_args()
assert re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,80}', args.name)
original = ROOT/'runs'/('cp2k-water-cpu-20261020-'+args.partition.lower()+'-a')
assert (original/'results/build-complete').is_file()
run = ROOT/'runs'/('cp2k-water-cpu-'+args.name)
run.mkdir()
for name in ('work', 'control', 'results', 'runtime', 'cache'):
    (run/name).mkdir()
for name in ('controller', 'deployment/cp2k-water-scaling'):
    shutil.copytree(REPO/name, run/'control'/name, ignore=shutil.ignore_patterns('__pycache__'))
inputs = {str(p.relative_to(run)): hashlib.sha256(p.read_bytes()).hexdigest()
          for p in (run/'control').rglob('*') if p.is_file()}
(run/'request.json').write_text(json.dumps(dict(partition=args.partition, original=str(original),
    source_sha='67b5da876dd6a76b8b021d5a04d1c81ba79a4c50', inputs=inputs), indent=2)+'\n')
qos = 'rush-1o2gpu' if args.partition == '4V100' else 'flood-1o2gpu'
job = subprocess.check_output(['sbatch', '--parsable', '--partition='+args.partition, '--qos='+qos,
    '--gpus-per-node=1', '--time=60', '--job-name=cp2k-mpi-repair-'+args.partition,
    '--output='+str(run/'results/slurm-%j.log'),
    str(run/'control/deployment/cp2k-water-scaling/relink-cpu.sbatch'), str(run), str(original)], text=True).strip()
assert job.isdigit(), job
(run/'job.id').write_text(job+'\n')
print(json.dumps(dict(job=job, run=str(run), partition=args.partition)))
