#!/usr/bin/env python3
"""Reparse completed candidate checks before publishing the DeePMD runtime."""
import hashlib
import json
import math
from pathlib import Path
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


root = Path(sys.argv[1]).resolve()
catalog_path = root / 'bundle/catalog.json'
catalog = json.loads(catalog_path.read_text())
runtime_hash = digest(root / 'bundle/libexec/sif_runtime.py')
assert runtime_hash == catalog['runtime_sha256']
reports = []
for partition, row in catalog['applications']['deepmd-kit']['partitions'].items():
    directory = root / partition
    result = json.loads((directory / 'result.json').read_text())
    assert result['passed'] and result['ldconfig']['passed']
    assert 'libcuda.so.1' in (directory / 'ldconfig.txt').read_text()
    for descriptor in ('se_e2_a', 'dpa2'):
        case = result[descriptor + '_single_gpu']
        assert case['passed']
        for stage, filename, final_step in (('training', 'lcurve.out', 8), ('restart', 'restart-lcurve.out', 12)):
            report = case[stage]
            assert report['passed'] and report['returncode'] == 0
            values = [[float(v) for v in line.split()]
                      for line in (directory / (descriptor + '-single') / filename).read_text().splitlines()
                      if line.strip() and not line.startswith('#')]
            assert values == report['learning_curve'] and values[-1][0] == final_step
            assert all(math.isfinite(v) for sample in values for v in sample)
        checkpoint = directory / (descriptor + '-single') / case['restart']['checkpoint']['name']
        assert digest(checkpoint) == case['restart']['checkpoint']['sha256']
    inference = result['inference']
    assert inference['passed'] and inference['device'] == 'cuda:0' and inference['cuda_bytes'] > 0
    assert all(math.isfinite(v) and v <= 1e-7 for v in inference['errors'].values())
    assert inference['package'].startswith(row['identity']['install_prefix'] + '/')
    # Deliberate negative check: the independent DDP defect remains visible.
    assert result['dpa2_ddp']['returncode'] != 0
    ddp_log = (directory / 'dpa2-ddp/training.log').read_text()
    assert 'Expected to have finished reduction' in ddp_log
    assert "No such file or directory: '/sbin/ldconfig'" not in ddp_log
    assert 'Batch       1:' in ddp_log
    traces = [json.loads(p.read_text()) for p in (directory / 'trace').glob('*.json')]
    assert len(traces) == 1
    [trace] = traces
    assert trace['software'] == 'deepmd-kit' and trace['sha256'] == row['sha256']
    assert trace['verification'] == 'hashed' and trace['memory_scope']['job'] == trace['job']
    assert result['jit_cache']['passed'] and result['jit_cache']['files'] > 0
    assert all(Path(path).is_relative_to(trace['memory_root']) for path in result['jit_cache']['paths'].values())
    assert '--nv' in trace['command'] and '/usr/sbin:/sbin:ro' in trace['command']
    assert '/etc/ld.so.cache:/etc/ld.so.cache:ro' in trace['command']
    report = dict(passed=True, automatic_memory=True, partition=partition, job=trace['job'],
                  runtime_sha256=runtime_hash, catalog_sha256=digest(catalog_path),
                  **{'deepmd-kit': result})
    (directory / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    reports.append(report)
(root / 'validation.json').write_text(json.dumps(reports, indent=2) + '\n')
print('Verified ldconfig, CUDA inference and single-GPU training/restart on all three partitions; '
      'the DDP unused-parameter failure is preserved.')
