#!/usr/bin/env python3
"""Archive terminal multi-node runs, including preflight failures and the pilot."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
PROJECT = Path('/home/stardust/sai-hpc-software')
manifest = json.loads((HERE/'manifest.json').read_text())
destination = Path(sys.argv[1]).resolve()
assert destination.is_relative_to(PROJECT/'diagnostics') and not destination.exists()

for row in manifest['runs']:
    root = PROJECT/'runtime-tests'/row['run']
    job = (root/'job.id').read_text().strip()
    assert job == row['job']
    accounting = subprocess.check_output(['sacct', '-X', '-j', job, '-n', '-P', '-o',
        'JobID,Partition,State%40,ExitCode,Elapsed,NodeList'], text=True)
    fields = next(line.split('|') for line in accounting.splitlines() if line.startswith(job+'|'))
    assert fields[1] == '16V100' and fields[2].split()[0] == row['state'], fields
    if row['kind'] == 'science':
        assert fields[3] == '0:0'
    (root/'results/accounting.txt').write_text(accounting)

destination.mkdir(parents=True)
shutil.copyfile(HERE/'manifest.json', destination/'manifest.json')
reconstructed = {}
for row in manifest['runs']:
    root = PROJECT/'runtime-tests'/row['run']
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.is_symlink() or '__pycache__' in path.parts:
            continue
        if path.suffix not in {'.json', '.txt', '.tsv', '.inp', '.py', '.sh', '.sbatch', '.md', '.log', '.xml'} and path.name not in {'forces.xyz', 'job.id'}:
            continue
        target = destination/row['label']/path.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    archived = destination/row['label']
    if (archived/'results/evidence.json').exists():
        evidence = json.loads((archived/'results/evidence.json').read_text())
        for name, digest in evidence['files'].items():
            if (archived/name).exists():
                continue
            # Older frozen runners recorded the site-managed topology that the
            # mapping script removes at EXIT. Restore only its proven exact bytes
            # in this archive, preserving the original run's absence and records.
            assert re.fullmatch(r'\.nccl_topo_'+row['job']+r'\.xml', name), name
            kept = archived/'results/nccl-topology.xml'
            assert hashlib.sha256(kept.read_bytes()).hexdigest() == digest
            shutil.copyfile(kept, archived/name)
            reconstructed[str((archived/name).relative_to(destination))] = dict(
                source=str(kept.relative_to(destination)), sha256=digest,
                reason='Site EXIT cleanup removed the transient name; retained topology has the recorded identical bytes')
(destination/'reconstructed-topology.json').write_text(json.dumps(reconstructed, indent=2, sort_keys=True)+'\n')
monitoring = PROJECT/'diagnostics/cp2k-container-multinode-monitor-20261021'
if monitoring.exists():
    (destination/'monitoring').mkdir()
    for path in monitoring.glob('*.json'):
        shutil.copyfile(path, destination/'monitoring'/path.name)
hashes = {str(p.relative_to(destination)): hashlib.sha256(p.read_bytes()).hexdigest()
          for p in sorted(destination.rglob('*')) if p.is_file()}
(destination/'SHA256.json').write_text(json.dumps(hashes, indent=2, sort_keys=True)+'\n')
print(json.dumps(dict(files_hashed=len(hashes), bytes=sum(p.stat().st_size for p in destination.rglob('*') if p.is_file()))))
