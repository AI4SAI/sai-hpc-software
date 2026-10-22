#!/usr/bin/env python3
"""Retry the exact 4V100 science task after restoring the account's real HOME."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path('/home/stardust/sai-hpc-software/experimental/deepmd-lammps')
BUILD = 'md-39403117741-1-2026-10-20-9bf6ae82814537d7-export'
PREVIOUS = '1879995'


def output(argv):
    return subprocess.check_output(list(map(str, argv)), text=True)


def main():
    os.umask(0o077)
    sys.dont_write_bytecode = True
    build = ROOT / 'runs' / BUILD
    control = Path(json.loads((build / 'request.json').read_text())['controller'])
    if control.resolve() != control or not control.is_relative_to(ROOT / 'controller'):
        raise ValueError('untrusted frozen controller')
    sys.path.insert(0, str(control))
    import md_acceptance_controller as science
    from md_publication import build_record
    from source_cache import checksum
    delivery, image, digest = build_record(BUILD)
    task = ROOT / 'runtime-tests' / (BUILD + '-science')
    request = json.loads((task / 'request.json').read_text())
    status = json.loads((task / 'results/status.json').read_text())
    if (request['build_run'] != BUILD or request['delivery'] != delivery
            or request['artifact_sha256'] != digest or request['artifact'] != str(image)
            or request['nodes'] != 2 or request['ranks'] != 4
            or (task / 'job.id').read_text().strip() != PREVIOUS
            or status.get('job') != PREVIOUS or status.get('state') != 'FAILED'
            or status.get('exit_code') != '1:0' or status.get('scientific_verified') is not False
            or request['job_script_sha256'] != checksum(task / 'job.sbatch')
            or (task / 'job.sbatch').read_text() != science.render(request, task)):
        raise ValueError('failed science task differs from recorded immutable candidate')
    if PREVIOUS in output(['squeue', '-h', '-u', 'stardust', '-o', '%A']).split():
        raise ValueError('previous science job is still active')
    accounting = output(['sacct', '-X', '-nP', '-j', PREVIOUS, '-o', 'JobIDRaw,State,ExitCode'])
    if accounting.strip().split('|')[:3] != [PREVIOUS, 'FAILED', '1:0']:
        raise ValueError('unexpected accounting')
    raw = task / 'results/baseline-tf-trials/0/stdout.txt'
    if ('Error: Unable to get the user home directory' not in raw.read_text()
            or 'Failed initializing opal: -1' not in raw.read_text()):
        raise ValueError('not the diagnosed host MPI initialization failure')
    record = dict(previous_job=PREVIOUS, artifact=str(image), artifact_sha256=digest,
                  failed_stdout_sha256=checksum(raw), original_request_sha256=checksum(task / 'request.json'),
                  original_script_sha256=checksum(task / 'job.sbatch'), recovery_tool_sha256=checksum(Path(__file__)),
                  scheduler_override={'SBATCH_QOS': 'rush-1o2gpu',
                                      'SBATCH_EXPORT': 'HOME=' + str(Path.home())},
                  reason='restore the real account HOME removed by Slurm export=NIL before host mpirun',
                  scientific_policy_unchanged=True, accounting=accounting)
    history = task / 'recovery-attempts' / PREVIOUS
    history.mkdir(parents=True)
    for name in ('request.json', 'job.id', 'job.sbatch', 'case', 'results', 'runtime'):
        (task / name).rename(history / name)
    environment = dict(os.environ, **record['scheduler_override'])
    subprocess.run(['python3', str(control / 'md_acceptance_controller.py'), 'submit',
                    task.name, BUILD, '--nodes', '2'], env=environment, check=True)
    job = (task / 'job.id').read_text().strip()
    if not job.isdigit() or job == PREVIOUS:
        raise ValueError('unexpected new science handle')
    if checksum(task / 'job.sbatch') != record['original_script_sha256']:
        raise ValueError('resubmission changed the frozen science script')
    record.update(job=job, scheduler=output(['scontrol', 'show', 'job', job]))
    request = json.loads((task / 'request.json').read_text())
    request['operational_recovery'] = record
    (task / 'request.json').write_text(json.dumps(request, sort_keys=True) + '\n')
    (history / 'retry.json').write_text(json.dumps(record, sort_keys=True) + '\n')
    print(json.dumps(record, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
