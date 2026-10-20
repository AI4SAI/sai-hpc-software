#!/usr/bin/env python3
"""Retry the pre-execution 16V100 cancellation with an explicit minimal environment."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path('/home/stardust/sai-hpc-software/experimental/deepmd-lammps')
BUILD = 'md-39403084059-1-2026-10-20-5fef92e054bacdaf-export'
PREVIOUS = '1880251'


def output(argv):
    return subprocess.check_output(list(map(str, argv)), text=True)


def main():
    os.umask(0o077)
    sys.dont_write_bytecode = True
    build = ROOT / 'runs' / BUILD
    control = Path(json.loads((build / 'request.json').read_text())['controller'])
    if control.resolve() != control or not control.is_relative_to(ROOT / 'controller'):
        raise ValueError('untrusted controller')
    sys.path.insert(0, str(control))
    import md_acceptance_controller as science
    from md_publication import build_record
    from source_cache import checksum
    delivery, image, digest = build_record(BUILD)
    task = ROOT / 'runtime-tests' / (BUILD + '-science')
    request = json.loads((task / 'request.json').read_text())
    status = json.loads((task / 'results/status.json').read_text())
    if (request['delivery'] != delivery or request['artifact_sha256'] != digest
            or request['artifact'] != str(image) or request['nodes'] != 2 or request['ranks'] != 4
            or (task / 'job.id').read_text().strip() != PREVIOUS
            or status.get('job') != PREVIOUS or status.get('state') != 'CANCELLED by 0'
            or status.get('exit_code') != '0:0' or status.get('scientific_verified') is not False
            or request['job_script_sha256'] != checksum(task / 'job.sbatch')
            or (task / 'job.sbatch').read_text() != science.render(request, task)):
        raise ValueError('unexpected source-bound scientific attempt')
    if PREVIOUS in output(['squeue', '-h', '-u', 'stardust', '-o', '%A']).split():
        raise ValueError('previous attempt is still active')
    accounting = output(['sacct', '-X', '-nP', '-j', PREVIOUS, '-o', 'JobIDRaw,State,ExitCode,Reason'])
    if accounting.strip().split('|')[:3] != [PREVIOUS, 'CANCELLED by 0', '0:0']:
        raise ValueError('unexpected cancellation accounting')
    if list((task / 'case').iterdir()) or {p.name for p in (task / 'results').iterdir()} != {'status.json'}:
        raise ValueError('previous task produced execution results; inspect before retrying')
    environment = dict(PATH='/usr/bin:/bin', HOME=str(Path.home()), USER='stardust', LOGNAME='stardust',
                       SAI_SOFTWARE_ROOT=str(ROOT.parent.parent), PYTHONDONTWRITEBYTECODE='1',
                       SBATCH_QOS='flood-1o2gpu', SBATCH_EXPORT='ALL')
    record = dict(previous_job=PREVIOUS, artifact=str(image), artifact_sha256=digest,
                  original_request_sha256=checksum(task / 'request.json'),
                  original_script_sha256=checksum(task / 'job.sbatch'), recovery_tool_sha256=checksum(Path(__file__)),
                  submission_environment=environment, accounting=accounting,
                  reason='scheduler cancelled before batch execution without a stated reason; retry explicit minimal environment',
                  scientific_policy_unchanged=True, resource_script_unchanged=True)
    history = task / 'recovery-attempts' / PREVIOUS
    history.mkdir(parents=True)
    for name in ('request.json', 'job.id', 'job.sbatch', 'case', 'results', 'runtime'):
        (task / name).rename(history / name)
    subprocess.run(['python3', str(control / 'md_acceptance_controller.py'), 'submit',
                    task.name, BUILD, '--nodes', '2'], env=environment, check=True)
    job = (task / 'job.id').read_text().strip()
    if not job.isdigit() or job == PREVIOUS or checksum(task / 'job.sbatch') != record['original_script_sha256']:
        raise ValueError('resubmission differs from frozen science script')
    record.update(job=job, scheduler=output(['scontrol', 'show', 'job', job]))
    request = json.loads((task / 'request.json').read_text())
    request['operational_recovery'] = record
    (task / 'request.json').write_text(json.dumps(request, sort_keys=True) + '\n')
    (history / 'retry.json').write_text(json.dumps(record, sort_keys=True) + '\n')
    print(json.dumps(dict(job=job, run=task.name, previous_job=PREVIOUS)), flush=True)


if __name__ == '__main__':
    main()
