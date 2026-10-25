#!/usr/bin/env python3
"""Run independent science on unchanged, build-verified campaign SIFs."""
from pathlib import Path
import io
import json
import shlex
import subprocess
import sys
import tarfile

repository = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(repository / 'controller'))
from deepmd_standalone import FILES, GPU_TARGETS
from deepmd_acceptance import acceptance_fingerprint

revision, *targets = sys.argv[1:]
assert revision.isdigit() and targets and all(target in GPU_TARGETS for target in targets)
control = '/org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-standalone/controller/ddp-science-20261025-r' + revision
ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', 'SAI-stardust']
if subprocess.run(ssh + [shlex.join(['test', '-d', control])]).returncode:
    subprocess.run(ssh + [shlex.join(['mkdir', control])], check=True)
    payload = io.BytesIO()
    files = (*FILES, 'deepmd_acceptance.py')
    with tarfile.open(fileobj=payload, mode='w') as archive:
        for name in files:
            archive.add(repository / 'controller' / name, arcname=name)
    subprocess.run(ssh + [shlex.join(['tar', '-xf', '-', '-C', control])], input=payload.getvalue(), check=True)
    subprocess.run(ssh + [shlex.join(['chmod', '0444', *[control + '/' + name for name in files]])], check=True)
code = 'import sys; sys.path.insert(0, sys.argv[1]); from deepmd_acceptance import acceptance_fingerprint; print(acceptance_fingerprint())'
assert subprocess.check_output(ssh + [shlex.join(['python3', '-c', code, control])], text=True).strip() == acceptance_fingerprint()
for target in targets:
    source = 'ddp-20261025-r2-' + target
    run = source.replace('-r2-', '-science-r' + revision + '-')
    command = ['env', 'PYTHONDONTWRITEBYTECODE=1', 'python3', control + '/deepmd_acceptance.py',
               'submit', run, '--source-run', source]
    record = json.loads(subprocess.check_output(ssh + [shlex.join(command)], text=True))
    Path(__file__).with_name('acceptance-r' + revision + '-' + target + '.json').write_text(json.dumps(record, indent=2) + '\n')
    print(target, record['job_id'], flush=True)
