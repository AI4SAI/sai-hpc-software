#!/usr/bin/env python3
"""Qualify an existing compiled prefix without mutating or recompiling it."""
from datetime import datetime
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from prepare import (APP, BASE, PACKAGE, PREFIX, PROJECT, ROOT, SOURCE, check,
                     container, digest, private, save)

origin = Path(sys.argv[1])
assert origin.parent == ROOT / 'runs'
check(origin)
run = ROOT / 'runs' / ('qualification-3.1.5-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
run.mkdir()
for name in ('control', 'runtime', 'results'):
    private(run / name)
for name in ('prepare.py', 'qualify.sh', 'qualify.py', 'science.py', 'gui-smoke.py'):
    shutil.copyfile(PACKAGE / name, run / 'control' / name)
    (run / 'control' / name).chmod(0o444)
shutil.copytree(origin / 'runtime/tests', run / 'runtime/tests')
request = json.loads((origin / 'request.json').read_text())
request['origin'] = str(origin)
request['pins'] = {str(p): digest(p) for p in [
    *sorted((run / 'control').iterdir()), *sorted((ROOT / 'cache/wheels').glob('*.whl'))]}
payload = origin / 'rootfs' / PREFIX.lstrip('/')
request['native_tree'] = {str(p.relative_to(payload)): dict(
    kind='link' if p.is_symlink() else 'file',
    value=os.readlink(p) if p.is_symlink() else digest(p))
    for p in payload.rglob('*') if not p.is_dir()}
save(run / 'request.json', request)
image = run / 'neptrainkit-3.1.5.sif'
build_command = container(run, BASE, ['/bin/bash', '/control/qualify.sh'])
build_command[2:2] = ['--bind', str(payload) + ':' + PREFIX + ':ro',
                      '--bind', str(ROOT / 'cache/wheels') + ':/input/wheels:ro']
q = shlex.join
script = '\n'.join([
    '#!/bin/bash', '#SBATCH --job-name=neptrainkit-qualify', '#SBATCH --partition=16V100',
    '#SBATCH --qos=flood-1o2gpu', '#SBATCH --account=proj-sai-hpc-software',
    '#SBATCH --nodes=1', '#SBATCH --ntasks=1', '#SBATCH --gpus-per-node=1',
    '#SBATCH --time=00:20:00', '#SBATCH --export=NIL', '#SBATCH --chdir=' + str(run),
    '#SBATCH --output=' + str(run / 'results/slurm-%j.log'),
    'set -euo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""',
    'export HOME=' + shlex.quote(str(Path.home())) + ' USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?}',
    'export TMPDIR=' + str(run / 'runtime/tmp'),
    'export APPTAINER_TMPDIR="$TMPDIR" APPTAINER_CACHEDIR=' + str(run / 'runtime/apptainer-cache'),
    'export APPTAINERENV_CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:?}"',
    'mkdir -p "$TMPDIR" "$APPTAINER_CACHEDIR"',
    'nvidia-smi --query-gpu=name,uuid,driver_version --format=csv,noheader > results/gpus.txt',
    q(['/usr/bin/python3', '-B', str(run / 'control/prepare.py'), 'check', str(run)]),
    q(build_command),
    q(['mksquashfs', str(origin / 'rootfs'), str(run / 'final.squashfs'), '-noappend',
       '-all-root', '-no-xattrs', '-processors', '4', '-no-progress']),
    q([APP, 'sif', 'new', str(image)]),
    q([APP, 'sif', 'add', '--datatype', '4', '--partfs', '1', '--parttype', '2',
       '--partarch', '2', '--groupid', '1', str(image), str(run / 'final.squashfs')]),
    q(container(run, image, [PREFIX + '/bin/python', '-B', '/control/science.py',
       '/runtime/tests/data/nep', '/runtime/sif-science.json'])),
    q(container(run, image, [PREFIX + '/bin/python', '-B', '/control/gui-smoke.py',
       '/runtime/sif-gui.json'])),
    q(['/usr/bin/python3', '-B', str(run / 'control/prepare.py'), 'finalize', str(run)]),
    'echo NEPTRAINKIT_SIF_ACCEPTED', ''])
(run / 'job.sbatch').write_text(script)
subprocess.run(['bash', '-n', str(run / 'job.sbatch')], check=True)
job = subprocess.check_output(['sbatch', '--parsable', '--hold', str(run / 'job.sbatch')], text=True).strip().split(';')[0]
assert job.isdigit()
save(run / 'submission.json', dict(job=job, request_sha256=digest(run / 'request.json'), script_sha256=digest(run / 'job.sbatch')))
subprocess.run(['scontrol', 'release', job], check=True)
print(json.dumps(dict(job=job, run=str(run)), indent=2))
