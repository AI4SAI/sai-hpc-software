#!/usr/bin/env python3
"""Reparse all OT calculations with their original frozen runner, offline."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent / 'raw'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


hashes = json.loads((ROOT / 'SHA256.json').read_text())
assert {str(path.relative_to(ROOT)) for path in ROOT.rglob('*') if path.is_file()} == set(hashes) | {'SHA256.json'}
for name, expected in hashes.items():
    assert digest(ROOT / name) == expected, name
results = {}
submissions = json.loads((ROOT / 'submissions.json').read_text())
for target, submission in submissions['runs'].items():
    run = ROOT / target
    runner = run / 'control/deployment/cp2k-diagonalization/run.py'
    assert digest(runner) == submission['frozen_runner_sha256']
    assert digest(run / 'request.json') == submission['request_sha256']
    result = json.loads(subprocess.check_output(
        [sys.executable, '-B', str(runner), 'verify', str(run)], text=True))
    assert result == json.loads((run / 'validation.json').read_text())
    assert result['identity'] == submission['identity']
    assert result['artifact_sha256'] == submission['artifact_sha256']
    assert len(result['samples']) == 8
    # Independently confirm that each requested input pair changes only backend.
    cusolver = (run / 'fixtures/ot-cusolver.inp').read_text()
    elpa = (run / 'fixtures/ot-elpa.inp').read_text()
    assert cusolver.replace('PREFERRED_DIAG_LIBRARY CUSOLVER', 'PREFERRED_DIAG_LIBRARY ELPA') == elpa
    results[target] = {key: value for key, value in result.items() if key != 'samples'}
print(json.dumps({'passed': True, 'calculations': 24, 'files_hashed': len(hashes),
                  'revalidator_sha256': digest(Path(__file__)), 'results': results}, indent=2, sort_keys=True))
