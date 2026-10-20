#!/usr/bin/env python3
"""Advance the five accepted resource points without rerunning failed jobs."""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import sys
import time

import run as protocol


def scheduler(runs):
    ids = sorted({(Path(r)/'job.id').read_text().strip() for r in runs if (Path(r)/'job.id').is_file()})
    if not ids:
        return {}
    text = subprocess.check_output(['sacct', '-X', '-j', ','.join(ids), '-n', '-P',
        '-o', 'JobID,State,ExitCode,Elapsed,NodeList'], text=True, timeout=30)
    result = {}
    for row in text.splitlines():
        fields = row.strip().split('|')
        if len(fields) >= 5:
            result[fields[0]] = dict(state=fields[1], exit_code=fields[2], elapsed=fields[3], nodes=fields[4])
    return result


def state(path, states):
    job = Path(path)/'job.id'
    return states.get(job.read_text().strip(), {}).get('state', 'UNKNOWN') if job.is_file() else 'NOT_SUBMITTED'


def ensure_verified(path):
    record = Path(path)/'results/verified.json'
    if not record.is_file():
        protocol.verify(Path(path))
    else:
        data = json.loads(record.read_text())
        protocol.require(data['verified'] and data['request_sha256'] == protocol.digest(Path(path)/'request.json'),
                         'Verification belongs to a different request')


def submit(name, partition, gpus, stage, binary, *, shape=None, calibration=None, smoke=None):
    root = protocol.ROOT/'runtime-tests'/('cp2k-water-'+name)
    if root.exists():
        # Recover only a submission already recorded before a daemon restart.
        request = json.loads((root/'request.json').read_text())
        protocol.require((root/'job.id').is_file() and request['partition'] == partition and
                         request['gpus'] == gpus and request['stage'] == stage and request['cpu_binary'] == binary,
                         'Existing submission requires manual review: '+str(root))
        return str(root)
    minutes = 120 if stage == 'smoke' else (240 if partition == '16V100' and gpus == 1 else 1440)
    argv = [sys.executable, '-B', str(Path(__file__).with_name('run.py')), 'submit', '--name', name,
            '--partition', partition, '--gpus', str(gpus), '--stage', stage, '--cpu-binary', binary,
            '--minutes', str(minutes), '--case-seconds', '7200']
    if shape:
        argv += ['--shape', *map(str, shape), '--calibration-run', calibration, '--cpu-smoke-run', smoke]
    subprocess.run(argv, check=True, timeout=300)
    return str(root)


def tick(path):
    campaign = json.loads(path.read_text())
    runs = [row['build'] for row in campaign['cpu'].values()]
    runs += [row['smoke'] for row in campaign['cpu'].values() if row.get('smoke')]
    runs += [row[key] for row in campaign['points'] for key in ('calibration', 'comparison') if row.get(key)]
    states = scheduler(runs)
    errors = []
    for partition, row in campaign['cpu'].items():
        try:
            row['build_state'] = state(row['build'], states)
            if not row.get('smoke') and row['build_state'] == 'COMPLETED':
                row['smoke'] = submit(campaign['name']+'-'+partition.lower()+'-smoke', partition, 1, 'smoke',
                                      row['build']+'/install/bin/cp2k.psmp')
            if row.get('smoke'):
                row['smoke_state'] = state(row['smoke'], states)
                if row['smoke_state'] == 'COMPLETED':
                    ensure_verified(row['smoke'])
                    row['accepted'] = True
        except Exception as error:
            errors.append(f'{partition} CPU: {error}')
    for row in campaign['points']:
        try:
            row['calibration_state'] = state(row['calibration'], states)
            cpu = campaign['cpu'][row['partition']]
            if row['calibration_state'] == 'COMPLETED' and not row.get('comparison') and cpu.get('accepted'):
                ensure_verified(row['calibration'])
                memory = json.loads((Path(row['calibration'])/'results/calibration.json').read_text())
                protocol.require(memory['reason'] == 'all_gpus_in_target', 'Memory window requires review: '+memory['reason'])
                row['shape'] = memory['selected']['shape']
                row['comparison'] = submit(campaign['name']+'-'+row['partition'].lower()+'-g'+str(row['gpus'])+'-compare',
                    row['partition'], row['gpus'], 'compare', cpu['build']+'/install/bin/cp2k.psmp',
                    shape=row['shape'], calibration=row['calibration'], smoke=cpu['smoke'])
            if row.get('comparison'):
                row['comparison_state'] = state(row['comparison'], states)
                if row['comparison_state'] == 'COMPLETED':
                    ensure_verified(row['comparison'])
                    row['accepted'] = True
        except Exception as error:
            errors.append(f'{row["partition"]}/{row["gpus"]} GPU: {error}')
    campaign['updated_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    campaign['scheduler'] = states
    campaign['errors'] = errors
    campaign['complete'] = all(row.get('accepted') for row in campaign['points'])
    pending = path.with_suffix('.new.json')
    protocol.write_json(pending, campaign)
    pending.replace(path)
    print(json.dumps(dict(updated=campaign['updated_utc'], complete=campaign['complete'], errors=errors,
        points=[{k: row.get(k) for k in ('partition', 'gpus', 'calibration_state', 'comparison_state', 'accepted')}
                for row in campaign['points']]), sort_keys=True), flush=True)
    return campaign['complete']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('campaign', type=Path)
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    while True:
        if tick(args.campaign) or not args.watch:
            break
        time.sleep(60)
