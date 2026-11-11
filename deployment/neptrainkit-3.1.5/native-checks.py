#!/usr/bin/env python3
"""Submit a direct-host, single-GPU check of the exported native installation."""
from datetime import datetime
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import install
from prepare import PREFIX, PROJECT, digest

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/')
release = install.metadata()
install.check(release, root, installed=True)
prefix = root / PREFIX.lstrip('/')
origin = PROJECT / 'experimental/neptrainkit/runs/qualification-3.1.5-20261111-203608'
run = PROJECT / 'operations' / ('neptrainkit-native-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
run.mkdir()
for name in ('science.py', 'gui-smoke.py'):
    shutil.copyfile(install.PACKAGE / name, run / name)
shutil.copytree(origin / 'runtime/tests/data/nep', run / 'case')
(run / 'request.json').write_text(json.dumps(dict(prefix=str(prefix), route='physical-opt' if root == Path('/') else 'staged-native',
    runtime=release['runtime'], release_sha256=digest(install.PACKAGE / 'release.json'),
    files={str(p.relative_to(run)): digest(p) for p in run.rglob('*') if p.is_file()}), indent=2) + '\n')
q=shlex.quote
script='\n'.join([
    '#!/bin/bash', '#SBATCH --job-name=neptrainkit-native', '#SBATCH --partition=16V100',
    '#SBATCH --qos=flood-1o2gpu', '#SBATCH --account=proj-sai-hpc-software',
    '#SBATCH --nodes=1', '#SBATCH --ntasks=1', '#SBATCH --gpus-per-node=1',
    '#SBATCH --time=00:10:00', '#SBATCH --export=NIL', '#SBATCH --chdir=' + str(run),
    '#SBATCH --output=' + str(run / 'slurm-%j.log'), 'set -eo pipefail',
    'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" PYTHONPATH="" LD_PRELOAD=""',
    'export HOME=' + q(str(run / 'home')) + ' USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?}',
    'export TMPDIR=' + q(str(run / 'tmp')),
    'export XDG_RUNTIME_DIR=' + q(str(run / 'xdg')) + ' QT_QPA_PLATFORM=offscreen',
    'export NEPTRAINKIT_RUNTIME_ROOT=' + q(str(run / 'managed-nep')),
    'export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1',
    'mkdir -p "$HOME" "$TMPDIR" "$XDG_RUNTIME_DIR" "$NEPTRAINKIT_RUNTIME_ROOT"',
    'chmod 700 "$XDG_RUNTIME_DIR"',
    'env -u LD_LIBRARY_PATH ' + q(str(prefix / 'bin/python')) + ' -c ' + q(
        'import NepTrainKit._native._io, NepTrainKit._native._sampling, nep_adapters; from PySide6 import QtWidgets; print("NO_LD_LIBRARY_PATH_IMPORT_PASSED")') + ' > no-ld-library-path.txt 2>&1',
    'source /etc/profile.d/lmod.sh', 'module purge',
    'module use ' + q(str(root / 'opt/modules/modulefiles/apps')),
    'module load neptrainkit/3.1.5', 'set -u',
    'module -t list > modules.txt 2>&1',
    'export NEPTRAINKIT_EXPECT_PREFIX=' + q(str(prefix)),
    'ldd ' + q(str(prefix / 'bin/python')) + ' > loader.txt',
    q(str(prefix / 'bin/python')) + ' -c ' + q(
       'from pathlib import Path; import subprocess; import nep_adapters; p=Path(nep_adapters.__file__).parent; [subprocess.run(["ldd", str(s)],check=True) for s in sorted(p.glob("*.so"))]') + ' >> loader.txt',
    '! grep -q "not found" loader.txt',
    q(str(prefix / 'bin/python')) + ' -B science.py case science.json',
    q(str(prefix / 'bin/python')) + ' -B gui-smoke.py gui.json',
    'module unload neptrainkit/3.1.5', 'touch unload.passed',
    'echo NEPTRAINKIT_NATIVE_PASSED', ''])
(run / 'job.sbatch').write_text(script)
subprocess.run(['bash', '-n', str(run / 'job.sbatch')], check=True)
job=subprocess.check_output(['sbatch','--parsable',str(run/'job.sbatch')],text=True).strip().split(';')[0]
assert job.isdigit()
(run/'submission.json').write_text(json.dumps(dict(job=job, script_sha256=digest(run/'job.sbatch'),request_sha256=digest(run/'request.json')),indent=2)+'\n')
print(json.dumps(dict(job=job,run=str(run)),indent=2))
