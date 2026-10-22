#!/usr/bin/env python3
"""Capture this completed physical acceptance for offline raw-log revalidation.

Run on SAI-stardust, passing a new archive directory below the project home.
Only text evidence is copied; no SIFs, libraries, restart files or data payload.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
PROJECT = Path('/home/stardust/sai-hpc-software')
DIAGNOSTICS = PROJECT / 'diagnostics/cp2k-shared-elpa-physical-20261020'
SUBMISSION = json.loads((DIAGNOSTICS / 'submission.json').read_text())
RUN = Path(SUBMISSION['run'])
PACKAGE = Path(SUBMISSION['package'])
DEST = Path(sys.argv[1]).resolve()
assert DEST.is_relative_to(PROJECT / 'diagnostics') and not DEST.exists()


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy(source, relative):
    assert source.is_file() and not source.is_symlink(), source
    target = DEST / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def write(relative, value):
    (DEST / relative).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


# Reject unfinished/failed allocations before creating the archive.
jobs = ','.join(row['job'] for row in SUBMISSION['jobs'])
accounting = subprocess.check_output([
    'sacct', '-X', '-j', jobs, '-n', '-P',
    '--format=JobID,State,ExitCode,Elapsed,NodeList,Start,End'], text=True)
for row in SUBMISSION['jobs']:
    fields = next(line.split('|') for line in accounting.splitlines()
                  if line.startswith(row['job'] + '|'))
    assert fields[1:3] == ['COMPLETED', '0:0'], fields
DEST.mkdir(parents=True)
(DEST / 'accounting.txt').write_text(accounting)
for name in ['submission.json', 'scheduling-adjustment.json', 'submit.log']:
    copy(DIAGNOSTICS / name, name)
copy(Path('/opt/modules/modulefiles/apps/cp2k/2026.2-auto'), 'public-module.tcl')
for name, digest in SUBMISSION['package_sha256'].items():
    assert checksum(PACKAGE / name) == digest, name
    copy(PACKAGE / name, 'control/' + name)

release = json.loads((PACKAGE / 'release.json').read_text())
for spec in release['deliveries']:
    part = spec['partition']
    folder = RUN / part
    for name in ['native-acceptance.json', 'job.id', 'nodes.txt', 'allocation.txt',
                 'modules.txt', 'binary.txt', 'mpirun.txt', 'mpi-version.txt']:
        copy(folder / name, part + '/' + name)
    job = (folder / 'job.id').read_text().strip()
    copy(folder / ('slurm-' + job + '.log'), part + '/slurm-' + job + '.log')
    for path in sorted(folder.rglob('*')):
        if not path.is_file() or path.is_symlink():
            continue
        relative = str(path.relative_to(folder))
        if (relative.startswith(('cases/', 'version/')) and
                (path.name in {'input.inp', 'cp2k.log', 'forces.xyz', 'elapsed.txt'}
                 or '/ranks/' in relative)):
            copy(path, part + '/' + relative)
    reference = 'references/' + part + '/'
    sidecar = (PROJECT / 'containers/software/cp2k/release' / spec['build_id'] /
               part / (spec['run_id'] + '.json'))
    copy(sidecar, reference + 'sidecar.json')
    accepted = json.loads(sidecar.read_text())
    proof = accepted['cp2k_science']
    copy(PROJECT / 'runtime-tests' / proof['run_id'] / 'results/evidence.json',
         reference + 'evidence.json')
    control = Path(accepted['controller'])
    for path in sorted(control.glob('*.py')):
        copy(path, reference + 'controller/' + path.name)
    prefix = Path('/opt/apps/cp2k') / ('cp2k-release-' + spec['build_id']) / part
    copy(prefix / 'share/sai/deployment.json', reference + 'receipt.json')
    copy(prefix / 'share/sai/site-elpa.json', reference + 'site-elpa.json')
    data = {}
    for path in sorted((prefix / 'share/cp2k/data').rglob('*')):
        relative = str(path.relative_to(prefix))
        if path.is_symlink():
            data[relative] = {'kind': 'symlink', 'target': os.readlink(path)}
        elif path.is_file():
            data[relative] = {'kind': 'file', 'sha256': checksum(path)}
    write(reference + 'installed-data.json', data)

hashes = {str(path.relative_to(DEST)): checksum(path)
          for path in sorted(DEST.rglob('*')) if path.is_file()}
write('SHA256.json', hashes)
print(json.dumps({'archive': str(DEST), 'files_hashed': len(hashes),
                  'bytes': sum(path.stat().st_size for path in DEST.rglob('*') if path.is_file())}))
