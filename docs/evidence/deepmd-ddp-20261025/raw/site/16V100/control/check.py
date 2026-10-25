#!/usr/bin/env python3
"""Accept the patched SIF through the actual user runtime, including DDP."""
import hashlib
import json
import math
import os
from pathlib import Path
import runpy
import shutil
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


assert 'TRITON_CACHE_DIR' not in os.environ
from triton import knobs
cache_paths = dict(cache=knobs.cache.dir, dump=knobs.cache.dump_dir, override=knobs.cache.override_dir)
assert all(Path(value).is_relative_to(os.environ['TMPDIR']) for value in cache_paths.values())
os.environ['TORCH_DISTRIBUTED_DEBUG'] = 'DETAIL'
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
ddp = dict(cases={})
for descriptor in ('se_e2_a', 'dpa2'):
    directory = root / (descriptor + '-ddp')
    audit.prepare(directory, descriptor)
    first = audit.train(directory)
    if first.get('passed'):
        for rank in range(2):
            shutil.copyfile(directory / f'rank-{rank}.json', directory / f'initial-rank-{rank}.json')
    resumed = audit.train(directory, restart=True) if first.get('passed') else {}
    case = dict(training=first, restart=resumed, passed=bool(first.get('passed') and resumed.get('passed')))
    if case['passed']:
        assert all(not rank['empty_trainable_parameters'] and not rank['find_unused_parameters']
                   and (rank['backend'] == 'nccl' or 'cuda:nccl' in rank['backend'].split(','))
                   for stage in (first, resumed) for rank in stage['ranks'])
    ddp['cases'][descriptor] = case
    report['pytorch_ddp'] = ddp
    audit.write('result.json', report)
ddp['passed'] = all(case['passed'] for case in ddp['cases'].values())
report['pytorch_ddp'] = ddp
# An existing pre-fix single-GPU checkpoint must remain restartable.
compatibility = {}
for descriptor in ('se_e2_a', 'dpa2'):
    directory = root / (descriptor + '-compatibility')
    audit.prepare(directory, descriptor)
    shutil.copyfile(root / 'old-checkpoints' / (descriptor + '.pt'), directory / 'model.ckpt-8.pt')
    compatibility[descriptor] = single(directory, restart=True)
report['old_checkpoint_restart'] = dict(passed=all(row.get('passed') for row in compatibility.values()), cases=compatibility)
# Compress a freshly trained se_e2_a model and compare its force/energy/virial.
import numpy as np
from deepmd.infer import DeepPot
directory = root / 'se_e2_a-ddp'
for arguments, log_name in [(['freeze', '-o', 'frozen.pth'], 'freeze.log'),
                            (['compress', '-i', 'frozen.pth', '-o', 'compressed.pth', '-t', 'input.json'], 'compress.log')]:
    with (directory / log_name).open('w') as output:
        subprocess.run([sys.executable, '-m', 'deepmd', '--pt', *arguments], cwd=directory,
                       stdout=output, stderr=subprocess.STDOUT, check=True, timeout=180)
fixture = json.loads((root / 'control/fixture.json').read_text())
args = (np.asarray(fixture['coordinates']).reshape(1, -1), np.asarray(fixture['box']).reshape(1, 9), fixture['atom_types'])
expected = DeepPot(str(directory / 'frozen.pth')).eval(*args)
observed = DeepPot(str(directory / 'compressed.pth')).eval(*args)
errors = {}
for name, left, right in zip(('energy', 'force', 'virial'), expected, observed):
    np.testing.assert_allclose(right, left, atol=1e-5, rtol=1e-5)
    errors[name] = float(np.max(np.abs(right - left)))
report['compression'] = dict(passed=True, max_absolute_errors=errors)
audit.write('result.json', report)
os.chdir(root / 'inference')
catalog = json.loads((root / 'catalog.json').read_text())
os.environ['SAI_CHECK_PREFIX'] = catalog['applications']['deepmd-kit']['partitions'][os.environ['SLURM_JOB_PARTITION']]['identity']['install_prefix']
runpy.run_path('check.py', run_name='__main__')
report['inference'] = json.loads(Path('result.json').read_text())
os.chdir(root)
cache_files = list(Path(knobs.cache.dir).rglob('*'))
assert any(p.suffix in ('.so', '.cubin') for p in cache_files)
report['jit_cache'] = dict(passed=True, paths=cache_paths,
    files=sum(p.is_file() for p in cache_files),
    bytes=sum(p.stat().st_size for p in cache_files if p.is_file()))
report['passed'] = all(report[key]['passed'] for key in ('ldconfig', 'se_e2_a_single_gpu', 'dpa2_single_gpu', 'pytorch_ddp', 'old_checkpoint_restart', 'compression', 'inference'))
audit.write('result.json', report)
assert report['passed'], report
