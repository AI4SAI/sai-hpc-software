#!/usr/bin/env python3
"""Collect the three completed, previously submitted OT backend experiments."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

ROOT = Path('/home/stardust/sai-hpc-software')
DEST = ROOT / 'diagnostics/cp2k-ot-backends-20261020'
SUBMISSIONS = (ROOT / 'development/workflow-closure/docs/evidence/'
               'cp2k-water216-tuning-20261020/ot-submissions.json')
assert not DEST.exists()
DEST.mkdir()
RAW = DEST / 'raw'
RAW.mkdir()
shutil.copyfile(SUBMISSIONS, RAW / 'submissions.json')
submissions = json.loads(SUBMISSIONS.read_text())
summary = {}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


for target, record in submissions['runs'].items():
    run = Path(record['run'])
    request = json.loads((run / 'request.json').read_text())
    runner = run / 'control/deployment/cp2k-diagonalization/run.py'
    assert digest(runner) == record['frozen_runner_sha256']
    assert digest(run / 'request.json') == record['request_sha256']
    assert request['identity'] == record['identity']
    accounting = subprocess.check_output([
        'sacct', '-X', '-j', record['job_id'], '-n', '-P',
        '-o', 'JobID,Partition,State,ExitCode,Elapsed,NodeList'], text=True)
    fields = next(line.split('|') for line in accounting.splitlines()
                  if line.startswith(record['job_id'] + '|'))
    assert fields[2:4] == ['COMPLETED', '0:0'], fields
    (run / 'results/accounting.txt').write_text(accounting)
    validated = json.loads(subprocess.check_output(
        [sys.executable, '-B', str(runner), 'verify', str(run)], text=True))
    evidence = json.loads((run / 'results/evidence.json').read_text())
    names = set(request['inputs']) | set(evidence['files'])
    names.update(['request.json', 'job.id', 'results/evidence.json',
                  'results/accounting.txt', 'results/passed.txt', 'results/order.json',
                  'results/allocation.txt', 'results/hostname.txt', 'results/modules.txt',
                  'results/gpu.txt', 'results/slurm-' + record['job_id'] + '.log'])
    for name in sorted(names):
        source = run / name
        assert source.is_file() and not source.is_symlink(), source
        destination = RAW / target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    (RAW / target / 'validation.json').write_text(json.dumps(validated, indent=2, sort_keys=True) + '\n')
    summary[target] = {k: v for k, v in validated.items() if k != 'samples'}
    print(target, validated['median_seconds'], flush=True)
(RAW / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')
hashes = {str(path.relative_to(RAW)): digest(path) for path in sorted(RAW.rglob('*')) if path.is_file()}
(RAW / 'SHA256.json').write_text(json.dumps(hashes, indent=2, sort_keys=True) + '\n')
archive = DEST / 'raw.tar.gz'
with tarfile.open(archive, 'w:gz') as stream:
    stream.add(RAW, arcname='raw')
print(json.dumps({'remote_archive': str(archive), 'archive_sha256': digest(archive),
                  'bytes': archive.stat().st_size, 'files_hashed': len(hashes)}))
