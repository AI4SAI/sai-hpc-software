#!/usr/bin/env python3
"""Verify the three isolated ELPA science jobs; never publish or install them."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess
import time

from submit import ROOT

TARGETS = ('16v100-avx2', '4v100-avx512', '8v100v0-avx512')


def follow(target):
    task = ROOT / 'runtime-tests' / ('cp2k-elpa-host-staging-20261020-' + target)
    job = (task / 'job.id').read_text().strip()
    if not job.isdigit():
        raise ValueError('ambiguous job handle')
    deadline = time.monotonic() + 604800
    while time.monotonic() < deadline:
        queue = subprocess.check_output(['squeue', '-h', '-j', job, '-o', '%T'], text=True)
        if not queue.strip():
            with (task / 'results/verification.log').open('a') as stream:
                subprocess.run(['python3', str(Path(__file__).with_name('check_elpa_candidate.py')),
                                target, '--verify'], stdin=subprocess.DEVNULL, stdout=stream,
                               stderr=subprocess.STDOUT, check=True)
            return dict(job=job, diagnostic_verified=True, publication_allowed=False)
        time.sleep(45)
    raise TimeoutError('science still queued/running; not cancelled: ' + job)


def main():
    state = ROOT / 'runtime-tests/cp2k-elpa-host-staging-follow-20261020'
    state.mkdir(exist_ok=True)
    outcomes = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(follow, target): target for target in TARGETS}
        for future in as_completed(futures):
            target = futures[future]
            try:
                outcome = future.result()
            except Exception as exc:
                outcome = dict(diagnostic_verified=False, publication_allowed=False, error=str(exc))
            outcome['observed_unix'] = time.time()
            (state / (target + '.json')).write_text(json.dumps(outcome, indent=2) + '\n')
            outcomes[target] = outcome
            print(json.dumps({target: outcome}), flush=True)
    return 0 if all(v['diagnostic_verified'] for v in outcomes.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
