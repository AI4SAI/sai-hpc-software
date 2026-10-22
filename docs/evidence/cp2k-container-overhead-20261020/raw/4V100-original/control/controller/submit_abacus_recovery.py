#!/usr/bin/env python3
"""Retry a diagnosed beta9 build with a frozen recipe and explicit budget."""
import argparse
import json
import os
import subprocess
from types import SimpleNamespace

import software_controller as controller
from abacus_dependencies import ARCHIVE_CACHE, cache_archives, load_lock
from recovery_budget import claim, receipt, status, write_new
from release_contract import claim_build


def require_failed_build(previous_run):
    previous = controller.task_dir(previous_run)
    request = json.loads((previous / 'request.json').read_text())
    if (request.get('software') != 'abacus' or request.get('version') != 'v3.11.0-beta9' or
            request.get('sha') != '149723287702677dc159c5e96fb98cbc3482f445' or
            request.get('target') != '16v100-avx2'):
        raise ValueError('recovery is scoped to the existing beta9 16V100 candidate')
    job = (previous / 'job.id').read_text().strip()
    if not job.isdigit():
        raise ValueError('invalid previous job receipt')
    # Query the user's queue: squeue -j rejects IDs already purged from the
    # queue, which is normal for failed jobs still present in accounting.
    queued = subprocess.check_output(['squeue', '-h', '-u', str(os.getuid()), '-o', '%i'], text=True)
    if job in [row.split()[0] for row in queued.splitlines() if row.strip()]:
        raise ValueError('previous build is still active; do not duplicate it')
    rows = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', job,
                                    '-o', 'JobIDRaw,State%40,ExitCode'], text=True).splitlines()
    values = next((row.split('|') for row in rows if row.split('|')[0] == job), None)
    terminal = {'FAILED', 'CANCELLED', 'TIMEOUT', 'OUT_OF_MEMORY', 'NODE_FAIL', 'PREEMPTED', 'BOOT_FAIL'}
    if not values or values[1].split()[0] not in terminal:
        raise ValueError('previous build is not a confirmed failed terminal job')
    return dict(run_id=previous_run, job_id=job, state=values[1], exit_code=values[2])


def submit(run_id, previous_run, reason):
    authority = controller.ROOT / 'diagnostics/recovery-20260920'
    remaining = status(authority)['remaining']
    if remaining is not None and remaining < 1:
        raise ValueError('authorized ABACUS attempts exhausted')
    previous = require_failed_build(previous_run)
    if not reason.strip():
        raise ValueError('a diagnostic reason and validated fix must be recorded')
    if not load_lock().get('full_features'):
        raise ValueError('beta9 recovery must retain the complete scientific profile')
    args = SimpleNamespace(software='abacus', run_id=run_id, target='16v100-avx2',
        sha='149723287702677dc159c5e96fb98cbc3482f445', version='v3.11.0-beta9',
        track='release', source_ref='v3.11.0-beta9', jobs=4, minutes=240,
        overlay_mb=32768, resume_run=None)
    run = controller.task_dir(run_id)
    if run.exists():
        raise ValueError('run already exists; inspect it rather than resubmitting')
    subprocess.run(['git', '--git-dir', str(controller.ROOT / 'cache/repositories/abacus'),
                    'cat-file', '-e', args.sha + '^{commit}'], check=True)
    cache_archives(controller.ROOT / ARCHIVE_CACHE, source=controller.CONTROL.parent)
    args.identity = controller.request_identity(args)
    controller.init(args)
    script = run / 'job.sbatch'
    script.write_text(controller.render_job(args))
    script.chmod(0o700)
    subprocess.run(['bash', '-n', str(script)], check=True)
    subprocess.run(['sbatch', '--test-only', str(script)], check=True)
    budget_path, budget = claim(authority, run_id, 'build', reason)
    claim_build(controller.ROOT, [args.identity], run_id, retry=True)
    write_new(run / 'request.json', dict(vars(args), controller=str(controller.CONTROL),
        recipe_sha256=controller.recipe_fingerprint('abacus'), contract_schema=controller.CONTRACT_SCHEMA,
        retry_authorization=budget, previous_failure=previous, published=False))
    # Do not retry this call on SSH/receipt ambiguity. The held job must be
    # accounted for before release; unresolved reservations block new claims.
    job = subprocess.check_output(['sbatch', '--hold', '--parsable', str(script)], text=True).strip().split(';')[0]
    if not job.isdigit():
        raise ValueError('invalid scheduler receipt; reconcile before any retry')
    with (run / 'job.id').open('x') as stream:
        stream.write(job + '\n')
    receipt(budget_path, budget, job, 'submitted-held', run)
    subprocess.run(['scontrol', 'release', job], check=True)
    receipt(budget_path, budget, job, 'released', run)
    return dict(job_id=job, run=str(run), attempt=budget['number'], limit=budget['limit'],
                install_prefix=args.identity['install_prefix'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('run_id')
    parser.add_argument('--previous-run', required=True)
    parser.add_argument('--reason', required=True, help='diagnosed failure and validated repair')
    args = parser.parse_args()
    print(json.dumps(submit(args.run_id, args.previous_run, args.reason)))


if __name__ == '__main__':
    main()
