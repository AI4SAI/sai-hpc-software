#!/usr/bin/env python3
"""Exercise the ldconfig fix, real DPA2 training and existing GPU inference."""
import hashlib
import json
import math
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time

import training_audit as audit


def single(directory, restart=False):
    command = [sys.executable, '-m', 'deepmd', '--pt', 'train',
               'restart.json' if restart else 'input.json']
    if restart:
        command += ['--restart', 'model.ckpt-8.pt']
    start = time.monotonic()
    with (directory / ('restart.log' if restart else 'training.log')).open('w') as output:
        result = subprocess.run(['timeout', '--kill-after=15s', '180s', *command], cwd=directory,
                                stdout=output, stderr=subprocess.STDOUT)
    report = dict(command=command, returncode=result.returncode, elapsed_seconds=time.monotonic() - start)
    if result.returncode:
        return report
    filename = 'restart-lcurve.out' if restart else 'lcurve.out'
    rows = [[float(v) for v in line.split()] for line in (directory / filename).read_text().splitlines()
            if line.strip() and not line.startswith('#')]
    expected = list(range(9, 13)) if restart else list(range(1, 9))
    assert [int(row[0]) for row in rows if not restart or row[0] > 8] == expected
    assert all(math.isfinite(v) for row in rows for v in row)
    checkpoint = directory / ('model.ckpt-12.pt' if restart else 'model.ckpt-8.pt')
    assert checkpoint.stat().st_size > 0
    report.update(passed=True, learning_curve=rows, checkpoint=dict(name=checkpoint.name,
        bytes=checkpoint.stat().st_size, sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest()))
    return report


scratch = Path(os.environ['TMPDIR']) / 'deepmd-ldconfig'
for name in ('triton', 'inductor', 'extensions', 'cache/torch/kernels', 'cuda'):
    (scratch / name).mkdir(parents=True, exist_ok=True)
os.environ.update(TRITON_CACHE_DIR=str(scratch / 'triton'),
                  TORCHINDUCTOR_CACHE_DIR=str(scratch / 'inductor'),
                  TORCH_EXTENSIONS_DIR=str(scratch / 'extensions'),
                  XDG_CACHE_HOME=str(scratch / 'cache'), CUDA_CACHE_PATH=str(scratch / 'cuda'),
                  TORCH_DISTRIBUTED_DEBUG='DETAIL')
audit.write('inventory.json', audit.inventory())
listing = subprocess.check_output(['/sbin/ldconfig', '-p'], text=True)
Path('ldconfig.txt').write_text(listing)
assert 'libcuda.so.1' in listing
from triton.backends.nvidia.driver import libcuda_dirs
directories = libcuda_dirs()
assert any((Path(path) / 'libcuda.so.1').is_file() for path in directories)
report = dict(ldconfig=dict(passed=True, libcuda_dirs=directories))
root = Path.cwd()
for descriptor in ('se_e2_a', 'dpa2'):
    directory = root / (descriptor + '-single')
    audit.prepare(directory, descriptor)
    first = single(directory)
    resumed = single(directory, restart=True) if first.get('passed') else {}
    report[descriptor + '_single_gpu'] = dict(training=first, restart=resumed,
        passed=bool(first.get('passed') and resumed.get('passed')))
    audit.write('result.json', report)
directory = root / 'dpa2-ddp'
audit.prepare(directory, 'dpa2')
report['dpa2_ddp'] = audit.train(directory)
audit.write('result.json', report)
os.chdir(root / 'inference')
catalog = json.loads((root / 'catalog.json').read_text())
os.environ['SAI_CHECK_PREFIX'] = catalog['applications']['deepmd-kit']['partitions'][os.environ['SLURM_JOB_PARTITION']]['identity']['install_prefix']
runpy.run_path('check.py', run_name='__main__')
report['inference'] = json.loads(Path('result.json').read_text())
os.chdir(root)
report['passed'] = all(report[key]['passed'] for key in ('ldconfig', 'se_e2_a_single_gpu', 'dpa2_single_gpu', 'inference'))
audit.write('result.json', report)
assert report['passed'], report
