#!/usr/bin/env python3
"""Collect textual evidence; retain models/checkpoints/traces in the project."""
import hashlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import tarfile

remote = r'''
from pathlib import Path
import hashlib, io, json, subprocess, sys, tarfile
root = Path('/org/org-open-sai/proj-sai-hpc-software/runtime-tests/deepmd-ddp-20261025')
builds = Path('/org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-standalone/runs')
publication = json.loads((root / 'publication.json').read_text())
assert json.loads((root / 'public-result.json').read_text())['passed']
inventory = {}
with tarfile.open(fileobj=sys.stdout.buffer, mode='w|gz') as archive:
    def add(path, name, include):
        if path.is_symlink():
            inventory[name] = dict(path=str(path), symlink=str(path.readlink()), archived=False)
        elif path.is_file():
            value = hashlib.sha256(path.read_bytes()).hexdigest()
            inventory[name] = dict(path=str(path), sha256=value, bytes=path.stat().st_size, archived=include)
            if include:
                archive.add(path, arcname=name)
    def tree(path, name, all_text=False):
        for file in sorted(path.rglob('*')):
            relative = file.relative_to(path)
            if any(part.startswith(('deepmd-training-', '__pycache__')) for part in relative.parts):
                continue
            include = file.suffix in ('.json', '.log', '.txt', '.out', '.csv', '.sh', '.sbatch', '.py')
            if all_text:
                include = file.suffix != '.sif'
            add(file, name + '/' + relative.as_posix(), include)
    for part in ('16V100', '4V100', '8V100V0'):
        tree(root / part, 'site/' + part)
    tree(root / 'public-check', 'public-check')
    tree(root / 'bundle', 'candidate-runtime', all_text=True)
    tree(Path(publication['runtime']), 'published-runtime', all_text=True)
    tree(Path('/home/stardust/sai-hpc-software/sif-beta/generations') / publication['generation'], 'generation', all_text=True)
    for file in root.iterdir():
        if file.is_file() and file.suffix in ('.json', '.txt', '.log'):
            add(file, 'campaign/' + file.name, True)
    jobs = []
    for task in sorted(builds.glob('ddp-20261025-*')):
        request = task / 'request.json'
        jobs.append(json.loads(request.read_text())['job_id'])
        add(request, 'runs/' + task.name + '/request.json', True)
        add(task / 'job.sbatch', 'runs/' + task.name + '/job.sbatch', True)
        tree(task / 'results', 'runs/' + task.name + '/results')
        if '-r2-' in task.name or '-science-r3-' in task.name:
            tree(task / 'runtime', 'runs/' + task.name + '/runtime')
    jobs += [row['job'] for row in json.loads((root / 'jobs.json').read_text())]
    jobs.append(json.loads((root / 'public-job.json').read_text())['job'])
    accounting = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', ','.join(jobs),
        '--format=JobID,State,ExitCode,Elapsed,NodeList,Account,WorkDir'])
    for name, data in [('accounting.txt', accounting),
                       ('retained-artifacts.json', (json.dumps(inventory, indent=2, sort_keys=True) + '\n').encode())]:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
'''
result = subprocess.check_output(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
                                  'SAI-stardust', shlex.join(['python3', '-c', remote])])
root = Path(__file__).resolve().parent
raw = root / 'raw'
raw.mkdir()
with tarfile.open(fileobj=io.BytesIO(result), mode='r:gz') as archive:
    archive.extractall(raw, filter='data')
hashes = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
          for path in sorted(raw.rglob('*')) if path.is_file()}
(root / 'SHA256.json').write_text(json.dumps(hashes, indent=2, sort_keys=True) + '\n')
print('Collected', len(hashes), 'files;', len(result), 'compressed bytes')
