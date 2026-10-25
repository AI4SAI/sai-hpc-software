#!/usr/bin/env python3
"""Recheck the archived training observations without cluster or GPU access."""
import hashlib
import json
import math
from pathlib import Path

root = Path(__file__).resolve().parent
raw = root / 'raw'
site = json.loads((raw / 'site-record.json').read_text())
count = 0
for partition in ('16V100', '4V100', '8V100V0'):
    for installation in ('release', 'site'):
        directory = raw / 'native' / partition / installation
        summary = json.loads((directory / 'summary.json').read_text())
        assert summary['passed'] and summary['partition'] == partition
        assert summary['installation'] == installation
        assert set(summary['cases']) == {'se_e2_a', 'dpa2'}
        for descriptor, case in summary['cases'].items():
            assert case['passed']
            for stage, final_step in (('training', 8), ('restart', 12)):
                result = case[stage]
                assert result['passed'] and result['returncode'] == 0
                curve_file = 'lcurve.out' if stage == 'training' else 'restart-lcurve.out'
                rows = [[float(v) for v in line.split()]
                        for line in (directory / descriptor / curve_file).read_text().splitlines()
                        if line.strip() and not line.startswith('#')]
                assert rows == result['learning_curve'] and rows[-1][0] == final_step
                assert all(math.isfinite(v) for row in rows for v in row)
                ranks = result['ranks']
                assert {r['rank'] for r in ranks} == {0, 1}
                assert {r['cuda_current_device'] for r in ranks} == {0, 1}
                assert len({r['final_parameters_sha256'] for r in ranks}) == 1
                for rank in ranks:
                    filename = ('' if stage == 'restart' else 'initial-') + f"rank-{rank['rank']}.json"
                    assert json.loads((directory / descriptor / filename).read_text()) == rank
                    assert rank['world_size'] == 2 and 'nccl' in rank['backend']
                    assert rank['gradient_calls'] > 0 and rank['gradients_finite']
                    assert rank['gradient_devices'] == [f"cuda:{rank['local_rank']}"]
                    assert rank['initial_parameters_sha256'] != rank['final_parameters_sha256']
                assert result['checkpoint']['bytes'] > 0
                assert len(result['checkpoint']['sha256']) == 64
            count += 1
    directory = raw / 'sif' / partition
    summary = json.loads((directory / 'summary.json').read_text())
    assert not summary['passed'] and all(not case['passed'] for case in summary['cases'].values())
    assert all(case['training']['returncode'] != 0 for case in summary['cases'].values())
    se_log = (directory / 'se_e2_a/training.log').read_text()
    dpa_log = (directory / 'dpa2/training.log').read_text()
    assert 'Expected to have finished reduction' in se_log and '8 9 10 11' in se_log
    assert "No such file or directory: '/sbin/ldconfig'" in dpa_log
    traces = [json.loads(p.read_text()) for p in (directory / 'trace').glob('*.json')]
    assert traces and all(t['memory_scope'] and '--nv' in t['command'] for t in traces)
    image = site['deepmd']['partitions'][partition]['image']
    image_sha = site['deepmd']['partitions'][partition]['sha256']
    assert all(image in trace['command'] and trace['sha256'] == image_sha
               and trace['job'] == summary['job'] for trace in traces)
assert count == 12
for installation in ('sif', 'release', 'site'):
    probe = json.loads((raw / 'frameworks' / installation / 'frameworks.json').read_text())
    assert len(probe['torch']['devices']) == 2 and not probe['horovod']
    assert probe['tensorflow']['built_with_cuda'] is False
    assert not probe['tensorflow']['gpu_devices'] and probe['jax']['devices'] == ['cpu:0']
manifest = json.loads((root / 'SHA256.json').read_text())
observed = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in raw.rglob('*') if p.is_file()}
assert observed == manifest
print(f'Verified {len(observed)} archived files, 12 native training/restart cases, '
      '6 SIF failures and 3 framework probes.')
