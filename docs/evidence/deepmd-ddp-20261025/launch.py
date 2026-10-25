#!/usr/bin/env python3
"""Transport a frozen recipe and build the pinned source on three partitions."""
from pathlib import Path
import io
import json
import shlex
import subprocess
import sys
import tarfile

repository = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(repository / 'controller'))
from deepmd_standalone import FILES, fingerprint

revision = sys.argv[1]
assert revision.isdigit()
root = '/org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-standalone'
control = root + '/controller/ddp-20261025-r' + revision
ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', 'SAI-stardust']
subprocess.run(ssh + [shlex.join(['mkdir', control])], check=True)
payload = io.BytesIO()
with tarfile.open(fileobj=payload, mode='w') as archive:
    for name in FILES:
        archive.add(repository / 'controller' / name, arcname=name)
subprocess.run(ssh + [shlex.join(['tar', '-xf', '-', '-C', control])], input=payload.getvalue(), check=True)
subprocess.run(ssh + [shlex.join(['chmod', '0444', *[control + '/' + name for name in FILES]])], check=True)
code = 'import sys; sys.path.insert(0, sys.argv[1]); from deepmd_standalone import fingerprint; print(fingerprint())'
observed = subprocess.check_output(ssh + [shlex.join(['python3', '-c', code, control])], text=True).strip()
assert observed == fingerprint()
source = dict(track='development', source_ref='master',
              source_sha='558f56962775098b8b554e341c3b37d2f86e6aeb', source_version='master-2026-10-23')
record = dict(controller=control, recipe_sha256=observed, source=source, builds=[])
for target in ('16v100-avx2', '4v100-avx512', '8v100v0-avx512'):
    run_id = 'ddp-20261025-r' + revision + '-' + target
    command = ['env', 'PYTHONDONTWRITEBYTECODE=1', 'SAI_SOFTWARE_ROOT=/org/org-open-sai/proj-sai-hpc-software',
               'python3', control + '/deepmd_standalone.py', 'submit', run_id,
               '--source', json.dumps(source), '--target', target]
    request = json.loads(subprocess.check_output(ssh + [shlex.join(command)], text=True))
    record['builds'].append(dict(run_id=run_id, request=request))
    Path(__file__).with_name('submission-r' + revision + '.json').write_text(json.dumps(record, indent=2) + '\n')
    print(target, request.get('job_id'), flush=True)
