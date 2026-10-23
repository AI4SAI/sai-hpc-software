#!/usr/bin/env python3
"""Record an authorized QoS-only change to one existing ABACUS science job.

Run with Python -I -B on the login node; arguments are job ID and stage suffix.
This never submits jobs or changes the frozen controller, script or request.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path('/org/org-open-sai/proj-sai-hpc-software')
SOURCE = '370ba685e6707b3625c5580ff9d02ef0afb248e8'
RUN = '39971072923-1-development-4v100-avx512-2026-10-23-' + SOURCE[:12]


def execute(*args):
    result = subprocess.run(args, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(str(args) + ': ' + result.stderr)
    return dict(command=list(args), returncode=result.returncode,
                stdout=result.stdout, stderr=result.stderr)


def fields(record):
    return dict(token.split('=', 1) for token in shlex.split(record['stdout']) if '=' in token)


def adjust(job, suffix):
    if not job.isdigit() or suffix not in ('multinode', 'gpu-features', 'pw', 'hse', 'deepks'):
        raise ValueError('unsupported job or stage')
    stage = ROOT / 'runtime-tests' / (('benchmark-' if suffix in ('pw', 'hse', 'deepks') else '')
                                    + RUN + '-' + suffix)
    if stage.resolve() != stage:
        raise ValueError('noncanonical task directory')
    request = json.loads((stage / 'request.json').read_text())
    identity = request['identity']
    if (identity['source_sha'] != SOURCE or identity['partition'] != '4V100'
            or identity['track'] != 'development' or identity['software'] != 'abacus'
            or (stage / 'job.id').read_text().strip() != job):
        raise ValueError('unexpected source or job identity')
    def hashes():
        return {name: hashlib.sha256((stage / name).read_bytes()).hexdigest()
                for name in ('request.json', 'job.sbatch', 'job.id')}
    def accounting():
        return execute('sacct', '-X', '-n', '-P', '-j', job,
            '--format=JobIDRaw,State,ExitCode,Partition,QOS,Timelimit,Elapsed,ReqTRES,AllocTRES,Account,WorkDir,Start,End')
    report = dict(schema=1, observed_at=datetime.now(timezone.utc).isoformat(),
        job=job, stage=str(stage), purpose='Use allowed project priority for an existing queued science job',
        submitted_new_job=False, frozen_inputs_before=hashes(),
        before_scontrol=execute('scontrol', 'show', 'job', '-o', job), before_sacct=accounting())
    before = fields(report['before_scontrol'])
    if (before.get('JobId') != job or before.get('Account') != 'proj-sai-hpc-software'
            or before.get('Partition') != '4V100' or before.get('WorkDir') != str(stage)
            or before.get('Command') != str(stage / 'job.sbatch')
            or not before.get('UserId', '').startswith('stardust(')):
        raise ValueError('unexpected allocation identity')
    if before['JobState'] != 'PENDING' or before['QOS'] != 'flood-1o2gpu':
        report.update(adjusted=False, reason='Job is no longer pending in flood-1o2gpu', verified=True)
        return report
    report['association'] = execute('sacctmgr', '-n', '-P', 'show', 'assoc', 'where',
        'user=stardust', 'account=proj-sai-hpc-software', 'format=User,Account,Partition,QOS,DefaultQOS')
    report['partition'] = execute('scontrol', 'show', 'partition', '4V100')
    report['qos_policy'] = execute('sacctmgr', '-n', '-P', 'show', 'qos', 'where',
        'name=flood-1o2gpu,rush-1o2gpu', 'format=Name,Priority,MaxWall,MinTRES,MaxTRES,Flags')
    if 'rush-1o2gpu' not in report['association']['stdout'] or 'rush-1o2gpu' not in report['partition']['stdout']:
        raise ValueError('requested QoS is not permitted')
    report['preflight'] = execute('sbatch', '--test-only', '--qos=rush-1o2gpu',
        '--chdir=' + str(stage), str(stage / 'job.sbatch'))
    current = fields(execute('scontrol', 'show', 'job', '-o', job))
    if current['JobState'] != 'PENDING' or current['QOS'] != before['QOS']:
        report.update(adjusted=False, reason='Job changed during admission check', verified=True)
        return report
    report['adjustment'] = execute('scontrol', 'update', 'JobId=' + job, 'QOS=rush-1o2gpu')
    report['after_scontrol'] = execute('scontrol', 'show', 'job', '-o', job)
    report['after_sacct'] = accounting()
    report['frozen_inputs_after'] = hashes()
    after = fields(report['after_scontrol'])
    invariant_keys = ('JobId', 'Account', 'Partition', 'WorkDir', 'Command', 'UserId',
                      'NumTasks', 'CPUs/Task', 'TimeLimit', 'ReqTRES')
    if (after['QOS'] != 'rush-1o2gpu' or any(before.get(k) != after.get(k) for k in invariant_keys)
            or report['frozen_inputs_before'] != report['frozen_inputs_after']):
        raise ValueError('unexpected change after scheduling adjustment')
    report.update(adjusted=True, verified=True)
    return report


if __name__ == '__main__':
    print(json.dumps(adjust(sys.argv[1], sys.argv[2]), indent=2, sort_keys=True))
