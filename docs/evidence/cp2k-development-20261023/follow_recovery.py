#!/usr/bin/env python3
"""Finish the two reviewed recovery runs through their original frozen controllers."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path('/org/org-open-sai/proj-sai-hpc-software')
CONTROLLER_COMMIT = '10992f818af71543bb39cb81e0f60c9f7ede9535'
SOURCE = 'cfe5e5d87aaf302953f14eb43587bef2411a8fb2'
RECIPE = 'ae6467ac2934deecf1d610c9d60cb8c5100cf931d2c27aaa66819ba85ca76a79'
RECOVERIES = {
    '4v100-avx512': ('-node-retry4', '1915249'),
    '8v100v0-avx512': ('-node-retry1', '1915260'),
}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    if path.is_symlink() or not path.is_file() or path.resolve() != path:
        raise ValueError('noncanonical input: ' + str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record(path, data):
    with path.open('x') as stream:
        json.dump(data, stream, sort_keys=True, indent=2)
        stream.write('\n')


def main(target):
    suffix, job = RECOVERIES[target]
    base = '39983165813-1-development-' + target + '-2026-10-23-cfe5e5d87aaf'
    run_id = base + suffix
    run = ROOT / 'runs' / run_id
    science_id = run_id + '-science'
    control = ROOT / 'controller' / CONTROLLER_COMMIT / base
    request = json.loads((run / 'request.json').read_text())
    plan = json.loads((run / 'input/retry-plan.json').read_text())
    receipt = json.loads((run / 'results/retry-submitted.json').read_text())
    allocation = json.loads((run / 'results/recovery-running-allocation.json').read_text())
    if (request['controller'] != str(control) or request['identity']['source_sha'] != SOURCE
            or request['identity']['recipe_sha256'] != RECIPE or request['target'] != target
            or (run / 'job.id').read_text().strip() != job or receipt['job_id'] != job
            or receipt['returncode'] != 0 or receipt['release']['returncode'] != 0
            or not receipt['original_small_files_unchanged']
            or not all(receipt['held_allocation_checks'].values())
            or allocation['job_id'] != job
            or not all(allocation['running_allocation_checks'].values())):
        raise ValueError('reviewed recovery identity/submission/allocation differs')
    controller_hashes = plan['controller_file_sha256']
    request_hash = digest(run / 'request.json')

    def check_inputs():
        if (digest(run / 'request.json') != request_hash
                or any(digest(control / name) != expected for name, expected in controller_hashes.items())):
            raise ValueError('original request or frozen controller changed')

    check_inputs()
    output = run / 'results/recovery-followup'
    output.mkdir(mode=0o700)
    cli = ['/usr/bin/python3', '-B', '-s']
    commands = [
        ('build-monitor', [*cli, str(control / 'software_controller.py'), 'monitor', run_id,
                           '--timeout', '43200', '--interval', '30']),
        ('science-submit', [*cli, str(control / 'cp2k_acceptance.py'), 'submit', science_id,
                            request['version'], target, '--build-run-id', run_id]),
        ('science-monitor', [*cli, str(control / 'cp2k_acceptance.py'), 'monitor', science_id,
                             '--timeout', '14400', '--interval', '30']),
        ('publish', [*cli, str(control / 'software_controller.py'), 'publish', run_id]),
    ]
    record(output / 'plan.json', dict(started_at=now(), run_id=run_id, job_id=job,
        science_run_id=science_id, request_sha256=request_hash,
        controller=str(control), controller_file_sha256=controller_hashes,
        followup_script_sha256=digest(Path(__file__).resolve()), commands=commands,
        rule='one execution of each stage; stop on any failure; never cancel or resubmit'))
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('PYTHON', 'SBATCH_', 'SLURM_'))}
    env.update(SAI_SOFTWARE_ROOT=str(ROOT), PYTHONDONTWRITEBYTECODE='1',
               PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin')
    try:
        for stage, argv in commands:
            check_inputs()
            record(output / (stage + '.started.json'), dict(started_at=now(), argv=argv))
            print(stage, 'started', now(), flush=True)
            with (output / (stage + '.stdout')).open('x') as out, (output / (stage + '.stderr')).open('x') as err:
                result = subprocess.run(argv, cwd=ROOT, env=env, stdout=out, stderr=err)
            record(output / (stage + '.finished.json'), dict(finished_at=now(), returncode=result.returncode))
            if result.returncode:
                raise RuntimeError(stage + ' returned ' + str(result.returncode))
        artifact = Path((run / 'artifact.path').read_text().strip())
        manifest = json.loads(artifact.with_suffix('.json').read_text())
        if not manifest['verified'] or not manifest['published'] or manifest['identity'] != request['identity']:
            raise ValueError('final delivery manifest is not verified/published for this request')
        record(output / 'completed.json', dict(completed_at=now(), artifact=str(artifact),
            artifact_sha256=manifest['sha256'], identity=manifest['identity'],
            verified=True, published=True, physical_opt_installed=False))
        print('completed', now(), flush=True)
    except Exception as error:
        record(output / 'failed.json', dict(failed_at=now(), error=repr(error),
            rule='inspect existing evidence; this helper will not retry or cancel'))
        raise


if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in RECOVERIES:
        raise SystemExit('usage: follow_recovery.py {4v100-avx512|8v100v0-avx512}')
    main(sys.argv[1])
