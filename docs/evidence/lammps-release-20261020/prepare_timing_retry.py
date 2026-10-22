#!/usr/bin/env python3
"""Retain the failed pre-calibration attempt before a new, pinned timing verifier."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path('/home/stardust/sai-hpc-software/experimental/deepmd-lammps')
BUILD = 'md-39403084059-1-2026-10-20-5fef92e054bacdaf-export'
JOB = '1881437'


def main():
    os.umask(0o077)
    task = ROOT / 'runtime-tests' / (BUILD + '-performance')
    request = json.loads((task / 'request.json').read_text())
    status = json.loads((task / 'results/status.json').read_text())
    if (request['build_run'] != BUILD or (task / 'job.id').read_text().strip() != JOB
            or status.get('job') != JOB or status.get('state') != 'FAILED'
            or status.get('exit_code') != '1:0' or status.get('performance_verified') is not False
            or (task / 'results/evidence.json').exists()
            or list((task / 'results').rglob('frozen.json'))
            or list((task / 'results').rglob('record.json'))):
        raise ValueError('not the known failure before a calibrated performance trial')
    trial = task / 'results/tf/calibration-00'
    if (json.loads((trial / 'process.json').read_text())['returncode'] != 0
            or 'exactly one measured LAMMPS engine interval is required' not in (task / 'results/tf.log').read_text()
            or 'Loop time of 10.1153 on 1 procs for 100 steps with 2058 atoms' not in (trial / 'stdout.txt').read_text()
            or 'Loop time of 0.108853 on 1 procs for 1 steps with 2058 atoms' not in (trial / 'stdout.txt').read_text()):
        raise ValueError('the retained failure differs from the diagnosed parser error')
    active = subprocess.check_output(['squeue', '-h', '-u', 'stardust', '-o', '%A'], text=True).split()
    accounting = subprocess.check_output(['sacct', '-X', '-nP', '-j', JOB, '-o', 'JobIDRaw,State,ExitCode'], text=True)
    if JOB in active or accounting.strip().split('|')[:3] != [JOB, 'FAILED', '1:0']:
        raise ValueError('performance attempt is not terminal')
    names = {'request.json', 'job.id', 'job.sbatch', 'case', 'results', 'runtime'}
    if {path.name for path in task.iterdir()} != names:
        raise ValueError('unexpected performance task files; inspect before recovery')
    hashes = {}
    for path in task.rglob('*'):
        if 'runtime' not in path.relative_to(task).parts and path.is_file():
            if path.is_symlink():
                raise ValueError('unexpected linked performance evidence')
            hashes[str(path.relative_to(task))] = hashlib.sha256(path.read_bytes()).hexdigest()
    history = task / 'recovery-attempts' / JOB
    history.mkdir(parents=True)
    for name in sorted(names):
        (task / name).rename(history / name)
    record = dict(previous_job=JOB, build_run=BUILD, archived_at=str(history), accounting=accounting,
                  reason='LAMMPS post no prints a one-step warmup Loop line; parser failed before freezing calibration',
                  files=hashes, helper_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  measured_comparison_completed=False, performance_regression_retest_used=False,
                  original_controller_recovery=request['controller_recovery'])
    (history / 'retry.json').write_text(json.dumps(record, sort_keys=True) + '\n')
    print(json.dumps(record, sort_keys=True))


if __name__ == '__main__':
    main()
