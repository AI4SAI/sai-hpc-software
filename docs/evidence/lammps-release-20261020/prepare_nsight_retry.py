#!/usr/bin/env python3
"""Preserve incomplete Nsight failures, including successful TF measurements."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path('/home/stardust/sai-hpc-software/experimental/deepmd-lammps')
CONTROL = ROOT / 'controller/acceptance/8f7d954b8fe7d1df641345474db94d13f5d84345/lammps-release-20261020'
BUILDS = {
    '16V100': ('md-39403084059-1-2026-10-20-5fef92e054bacdaf-export', '1881635'),
    '4V100': ('md-39403117741-1-2026-10-20-9bf6ae82814537d7-export', '1881749'),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', choices=BUILDS)
    args = parser.parse_args()
    os.umask(0o077)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(CONTROL))
    import md_performance_controller as performance
    from md_performance_evidence import verify_performance_evidence
    from delivery_layout import check_performance
    from source_cache import checksum
    build, job = BUILDS[args.target]
    task = ROOT / 'runtime-tests' / (build + '-performance')
    request = json.loads((task / 'request.json').read_text())
    delivery = performance.validate_request(request)
    performance.science_evidence(request)
    status = json.loads((task / 'results/status.json').read_text())
    failed = task / 'results/pt/trace-baseline'
    if (request['build_run'] != build or (task / 'job.id').read_text().strip() != job
            or status.get('job') != job or status.get('state') != 'FAILED' or status.get('exit_code') != '1:0'
            or status.get('performance_verified') is not False or (task / 'results/evidence.json').exists()
            or (task / 'results/pt/records.json').exists() or (task / 'results/jax').exists()
            or (failed / 'record.json').exists()
            or json.loads((failed / 'process.json').read_text())['returncode'] != 1
            or 'Cannot create CUDA config file' not in (failed / 'stdout.txt').read_text()):
        raise ValueError('not the diagnosed incomplete CUDA profiler setup failure')
    nsys = (task / 'results/nsys.path').read_text().strip()
    expected = dict(slurm_job=job, resources=performance.RESOURCES,
                    launcher_sha256=checksum(CONTROL / 'md_runtime.sh'), artifact=request['artifact'],
                    artifact_sha256=request['artifact_sha256'], target=request['target'],
                    candidate_executable=delivery['identities']['lammps']['install_prefix'] + '/bin/lmp',
                    baseline_executable=performance.BASELINE, nsys=nsys)
    tf = verify_performance_evidence(task / 'results/tf', task / 'case', backend='tf', expected=expected)
    # A setup retry may not erase an already observed performance regression.
    check_performance(tf['performance']['candidate']['median_engine_seconds'],
                      tf['performance']['baseline']['median_engine_seconds'])
    active = subprocess.check_output(['squeue', '-h', '-u', 'stardust', '-o', '%A'], text=True).split()
    accounting = subprocess.check_output(['sacct', '-X', '-nP', '-j', job, '-o', 'JobIDRaw,State,ExitCode'], text=True)
    if job in active or accounting.strip().split('|')[:3] != [job, 'FAILED', '1:0']:
        raise ValueError('profiler attempt is not terminal')
    names = {'request.json', 'job.id', 'job.sbatch', 'case', 'results', 'runtime'}
    if {path.name for path in task.iterdir()} - {'recovery-attempts'} != names:
        raise ValueError('unexpected task files')
    hashes = {}
    for name in sorted(names - {'runtime'}):
        path = task / name
        for entry in ([path] if path.is_file() else path.rglob('*')):
            if entry.is_symlink():
                raise ValueError('unexpected linked performance evidence')
            if entry.is_file():
                hashes[str(entry.relative_to(task))] = checksum(entry)
    history = task / 'recovery-attempts' / job
    history.mkdir(parents=True)
    for name in sorted(names):
        (task / name).rename(history / name)
    record = dict(previous_job=job, build_run=build, archived_at=str(history), accounting=accounting,
                  reason='Nsight requires writable literal container /tmp; bind dedicated home storage there',
                  files=hashes, helper_sha256=checksum(Path(__file__)), verified_tf=tf,
                  full_comparison_completed=False, performance_regression_retest_used=False,
                  original_controller_recovery=request['controller_recovery'])
    (history / 'retry.json').write_text(json.dumps(record, sort_keys=True) + '\n')
    print(json.dumps(record, sort_keys=True))


if __name__ == '__main__':
    main()
