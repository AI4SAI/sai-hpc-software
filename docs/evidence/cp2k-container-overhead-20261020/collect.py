#!/usr/bin/env python3
"""Collect terminal runs listed in manifest.json; retain text evidence only."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
PROJECT = Path('/home/stardust/sai-hpc-software')
HERE = Path(__file__).resolve().parent
manifest = json.loads((HERE / 'manifest.json').read_text())
destination = Path(sys.argv[1]).resolve()
assert destination.is_relative_to(PROJECT / 'diagnostics') and not destination.exists()


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# Do not archive still-changing experiments.
for row in manifest['runs']:
    root = PROJECT / 'runtime-tests' / row['run']
    job = (root / 'job.id').read_text().strip()
    accounting = subprocess.check_output([
        'sacct', '-X', '-j', job, '-n', '-P', '-o',
        'JobID,Partition,State,ExitCode,Elapsed,NodeList'], text=True)
    fields = next(line.split('|') for line in accounting.splitlines() if line.startswith(job + '|'))
    assert fields[1] == row['partition'] and fields[2] == row['state'], fields
    assert fields[3] == ('1:0' if row['kind'] == 'failed-native' else '0:0'), fields
    (root / 'results/accounting.txt').write_text(accounting)

destination.mkdir(parents=True)
shutil.copyfile(HERE / 'manifest.json', destination / 'manifest.json')
# Original job snapshots remain unchanged. The first snapshot's verifier had a
# warmup self-comparison indexing bug; archive the corrected verifier separately.
verifier = Path(manifest['science_verifier']['path'])
assert checksum(verifier) == manifest['science_verifier']['sha256']
target = destination / 'verifier/deployment/cp2k-container-overhead/run.py'
target.parent.mkdir(parents=True)
shutil.copyfile(verifier, target)
source_control = PROJECT / 'runtime-tests' / manifest['runs'][0]['run'] / 'control/controller'
shutil.copytree(source_control, destination / 'verifier/controller', ignore=shutil.ignore_patterns('__pycache__'))
for row in manifest['runs']:
    root = PROJECT / 'runtime-tests' / row['run']
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if '__pycache__' in relative.parts:
            continue
        if path.suffix not in {'.json', '.txt', '.tsv', '.inp', '.py', '.sh', '.sbatch', '.md', '.log'} and path.name not in {'forces.xyz', 'job.id'}:
            continue
        target = destination / row['label'] / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)

hashes = {str(path.relative_to(destination)): checksum(path)
          for path in sorted(destination.rglob('*')) if path.is_file()}
(destination / 'SHA256.json').write_text(json.dumps(hashes, indent=2, sort_keys=True) + '\n')
print(json.dumps(dict(archive=str(destination), files_hashed=len(hashes),
                     bytes=sum(p.stat().st_size for p in destination.rglob('*') if p.is_file()))))
