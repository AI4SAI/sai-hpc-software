#!/usr/bin/env python3
"""Finish an existing queued build and prepare an additive administrator handoff.

This only submits native checks and prepares files. It never installs in /opt.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import time

from nepadapters_release import build_report, require
from nepadapters_native import PHASES, submit, verify


def wait_job(job, deadline):
    require(job.isdigit(), 'invalid Slurm handle')
    last = None
    while time.monotonic() < deadline:
        text = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', job,
                                        '-o', 'JobIDRaw,State,ExitCode'], text=True)
        row = next((x.split('|') for x in text.splitlines() if x.split('|')[0] == job), None)
        state = row[1] if row else 'PENDING_ACCOUNTING'
        if state != last:
            print(job, state, flush=True)
            last = state
        if row and row[1:3] == ['COMPLETED', '0:0']:
            return
        require(state in ('PENDING_ACCOUNTING', 'PENDING', 'RUNNING', 'CONFIGURING',
                          'COMPLETING', 'SUSPENDED', 'REQUEUED'), 'Slurm job failed: ' + str(row))
        time.sleep(45)
    raise TimeoutError('Slurm wait expired; no acceptance or installation claimed')


def main(build, native, previous, destination):
    deadline = time.monotonic() + 24 * 3600
    wait_job((build / 'job.id').read_text().strip(), deadline)
    require(build_report(build)['accepted'], 'queued SIF failed acceptance')
    if not native.exists():
        submit(build, native)
    else:
        plan = json.loads((native / 'request.json').read_text())
        require(plan['build'] == str(build) and not plan['installed'], 'existing native handoff differs')
    for phase in PHASES:
        wait_job((native / (phase + '.id')).read_text().strip(), deadline)
    verify(native)
    old = json.loads((previous / 'release.json').read_text())
    handoffs = [dict(build=row['build'], native=row['native']) for row in old['partitions'].values()]
    handoffs.append(dict(build=str(build), native=str(native)))
    path = Path(__file__).resolve().parents[1] / 'deployment/nepadapters-1.0.4/prepare-release.py'
    spec = importlib.util.spec_from_file_location('nepa_prepare', path)
    prepare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare)
    prepare.prepare(handoffs, destination, previous=previous)
    subprocess.run(['bash', str(destination / 'install.sh')], check=True)
    print('NEPADAPTERS_ALL_PARTITIONS_ADMIN_HANDOFF_READY ' + str(destination), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('build', 'native', 'previous', 'destination'):
        p.add_argument('--' + name, type=Path, required=True)
    args = p.parse_args()
    main(*(getattr(args, name).resolve() for name in ('build', 'native', 'previous', 'destination')))
