#!/usr/bin/env python3
"""Follow the four export recoveries through frozen science gates and tuning."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import json
from pathlib import Path
import subprocess
import time

from submit import ROOT, TARGETS, name


def follow(target):
    build = ROOT / 'runs' / (name(target) + '-export')
    request = json.loads((build / 'request.json').read_text())
    control = Path(request['controller'])
    log_path = build / 'results/recovery-follow.log'
    with log_path.open('a', buffering=1) as log:
        def run(tool, *args):
            command = ['python3', str(control / tool), *args]
            log.write(json.dumps(command) + '\n')
            subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL)
        run('software_controller.py', 'monitor', build.name, '--timeout', '604800', '--interval', '45')
        science = build.name + '-science'
        science_dir = ROOT / 'runtime-tests' / science
        if not science_dir.exists():
            run('cp2k_acceptance.py', 'submit', science, 'v2026.2', target, '--build-run-id', build.name)
        elif not (science_dir / 'job.id').exists():
            raise ValueError('ambiguous earlier science submission: ' + science)
        run('cp2k_acceptance.py', 'monitor', science, '--timeout', '604800', '--interval', '45')
        run('software_controller.py', 'publish', build.name)
        if target != 'dsprhbm':
            tuning_name = '39293908513-' + target + '-water216'
            tuning = ROOT / 'runtime-tests' / ('cp2k-direct-' + tuning_name)
            if tuning.exists():
                raise ValueError('tuning already exists; inspect its receipt before any retry')
            tool = ROOT / 'development/cp2k-tuning-d36b666/deployment/cp2k-diagonalization/run.py'
            command = ['python3', str(tool), 'submit', '--build-run', build.name,
                       '--name', tuning_name, '--edge', '6', '--repeats', '3']
            subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL)
        log.write('CP2K_RECOVERY_ACCEPTED_AND_FOLLOWUP_SUBMITTED\n')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--targets', nargs='+', choices=TARGETS, default=TARGETS)
    args = parser.parse_args()
    state = ROOT / 'runtime-tests/cp2k-export-recovery-39293908513'
    state.mkdir(exist_ok=True)
    outcomes = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(follow, target): target for target in args.targets}
        for future in as_completed(futures):
            target = futures[future]
            try:
                future.result()
                outcomes[target] = dict(status='accepted-and-followup-submitted')
            except Exception as error:
                outcomes[target] = dict(status='failed', error=str(error))
            outcomes[target]['observed_unix'] = time.time()
            (state / (target + '.json')).write_text(json.dumps(outcomes[target], indent=2) + '\n')
            print(json.dumps({target: outcomes[target]}), flush=True)
    if any(item['status'] == 'failed' for item in outcomes.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
