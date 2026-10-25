#!/usr/bin/env python3
"""Reparse the real SIF training, restart, inference and compression evidence."""
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
    assert all(result[key]['passed'] for key in ('ldconfig', 'pytorch_ddp', 'compression',
               'old_checkpoint_restart', 'se_e2_a_single_gpu', 'dpa2_single_gpu', 'inference', 'jit_cache'))
    assert result['passed']
    patch = json.loads((directory / 'source-patch.json').read_text())
    inventory = json.loads((directory / 'inventory.json').read_text())
    assert inventory['source']['pt/train/training.py']['sha256'] == patch['patched_sha256']
    for descriptor in ('se_e2_a', 'dpa2'):
        for mode in ('single', 'ddp'):
            case = result[descriptor + '_single_gpu'] if mode == 'single' else result['pytorch_ddp']['cases'][descriptor]
            assert case['passed']
            for stage, filename, final in (('training', 'lcurve.out', 8), ('restart', 'restart-lcurve.out', 12)):
                report = case[stage]
                assert report['passed'] and report['returncode'] == 0
                work = directory / (descriptor + '-' + mode)
                values = [[float(v) for v in line.split()] for line in (work / filename).read_text().splitlines()
                          if line.strip() and not line.startswith('#')]
                assert values == report['learning_curve'] and values[-1][0] == final
                assert all(math.isfinite(v) for sample in values for v in sample)
                assert digest(work / report['checkpoint']['name']) == report['checkpoint']['sha256']
                if mode == 'ddp':
                    ranks = report['ranks']
                    assert {rank['rank'] for rank in ranks} == {0, 1}
                    assert {rank['cuda_current_device'] for rank in ranks} == {0, 1}
                    for rank in ranks:
                        name = ('initial-' if stage == 'training' else '') + f"rank-{rank['rank']}.json"
                        assert json.loads((work / name).read_text()) == rank
                        assert rank['world_size'] == 2
                        assert rank['backend'] == 'nccl' or 'cuda:nccl' in rank['backend'].split(',')
                        assert not rank['empty_trainable_parameters'] and not rank['find_unused_parameters']
                        assert rank['gradient_calls'] > 0 and rank['gradients_finite']
                        assert rank['gradient_devices'] == [f"cuda:{rank['local_rank']}"]
                        assert rank['initial_parameters_sha256'] != rank['final_parameters_sha256']
                    assert ranks[0]['final_parameters_sha256'] == ranks[1]['final_parameters_sha256']
    inference = result['inference']
    assert inference['device'] == 'cuda:0' and inference['cuda_bytes'] > 0
    assert all(math.isfinite(v) and v <= 1e-7 for v in inference['errors'].values())
    assert all(math.isfinite(v) and v <= 1e-5 for v in result['compression']['max_absolute_errors'].values())
    assert inference['package'].startswith(row['identity']['install_prefix'] + '/')
    traces = [json.loads(p.read_text()) for p in (directory / 'trace').glob('*.json')]
    [trace] = traces
    assert trace['software'] == 'deepmd-kit' and trace['sha256'] == row['sha256']
    assert trace['verification'] == 'hashed' and trace['memory_scope']['job'] == trace['job']
    assert result['jit_cache']['files'] > 0
    assert all(Path(path).is_relative_to(trace['memory_root']) for path in result['jit_cache']['paths'].values())
    report = dict(passed=True, automatic_memory=True, partition=partition, job=trace['job'],
                  runtime_sha256=runtime_hash, catalog_sha256=digest(catalog_path), **{'deepmd-kit': result})
    (directory / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    reports.append(report)
(root / 'validation.json').write_text(json.dumps(reports, indent=2) + '\n')
print('Verified two-GPU training/restart, single-GPU regression, old checkpoint compatibility, compression and inference on all three partitions.')
