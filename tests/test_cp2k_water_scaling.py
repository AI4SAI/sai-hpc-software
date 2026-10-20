"""Guard the scientific/resource distinctions in the large-water comparison."""
import importlib.util
import json
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('water_scaling', ROOT/'deployment/cp2k-water-scaling/run.py')
water = importlib.util.module_from_spec(spec)
spec.loader.exec_module(water)
campaign_spec = importlib.util.spec_from_file_location('water_campaign', ROOT/'deployment/cp2k-water-scaling/campaign.py')
campaign = importlib.util.module_from_spec(campaign_spec)
with patch.dict(sys.modules, {'run': water}):
    campaign_spec.loader.exec_module(campaign)


class WaterScalingTests(unittest.TestCase):
    def test_same_physics_and_calibration_is_explicitly_incomplete(self):
        gpu = water.input_text('gpu', (5, 6, 7))
        cpu = water.input_text('cpu', (5, 6, 7))
        self.assertEqual(gpu[gpu.index('&FORCE_EVAL'):], cpu[cpu.index('&FORCE_EVAL'):])
        self.assertEqual(sum(line.strip().startswith('O ') for line in gpu.splitlines()), 210)
        self.assertIn('ABC 15.5 18.6 21.7', gpu)
        self.assertNotIn('IGNORE_CONVERGENCE_FAILURE', gpu)
        self.assertIn('IGNORE_CONVERGENCE_FAILURE T', water.input_text('gpu', (5, 6, 7), True))
        self.assertIn('RUN_TYPE ENERGY\n', water.input_text('gpu', (5, 6, 7), True))
        self.assertIn('NROW_BLOCK 64', water.input_text('gpu', (5, 6, 7), block=64))

    def test_backend_timers_must_prove_dispatch(self):
        gpu = ' eigensolver_generalized 3 1.0 0.1 0.1 3.0 3.0\n cp_fm_general_cusolver 3 1.0 0.1 0.1 2.0 2.0\n'
        cpu = ' eigensolver 3 1.0 0.1 0.1 3.0 3.0\n cp_fm_syevd 3 1.0 0.1 0.1 2.0 2.0\n'
        water.timers(gpu, 'gpu'); water.timers(cpu, 'cpu')
        with self.assertRaises(ValueError):
            water.timers(cpu, 'gpu')
        with self.assertRaises(ValueError):
            water.timers(gpu+cpu, 'cpu')

    def test_no_duplicate_cards_or_overlapping_physical_cores(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            for i in range(4):
                record = dict(rank=i, gpu='GPU-'+str(i), cores={str(c): [0, c] for c in range(i*4, i*4+4)})
                (path/f'rank-{i}.json').write_text(json.dumps(record))
            water.verify_ranks(path, 'gpu', 4, 4, 4)
            record['gpu'] = 'GPU-0'
            (path/'rank-3.json').write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, 'GPU mapping'):
                water.verify_ranks(path, 'gpu', 4, 4, 4)
            record['gpu'] = 'GPU-3'; record['cores'] = {'0': [0, 0], '1': [0, 1], '2': [0, 2], '3': [0, 3]}
            (path/'rank-3.json').write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, 'Overlapping'):
                water.verify_ranks(path, 'gpu', 4, 4, 4)

    def test_size_selection_stays_in_bracket(self):
        shape = water.next_shape(550, 512, 729, {(8, 8, 8)})
        self.assertTrue(512 < water.math.prod(shape) < 729)
        self.assertIsNone(water.next_shape(512, 511, 513, {(8, 8, 8)}))

    def test_cleanup_never_signals_a_reused_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder/'rank-0.json').write_text(json.dumps(dict(pid=999999, start_ticks=10)))
            read_text = Path.read_text
            def proc_read(path, *args, **kwargs):
                if str(path) == '/proc/999999/stat':
                    fields = ['0']*22
                    fields[0] = 'S'; fields[19] = '11'
                    return '999999 (cp2k) '+' '.join(fields)
                return read_text(path, *args, **kwargs)
            with patch.object(Path, 'read_text', proc_read), patch.object(water.os, 'kill') as kill:
                self.assertEqual(water.cleanup_stopped_ranks(folder), [])
                kill.assert_not_called()

    def test_scheduler_failure_cannot_be_published_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder/'request.json').write_text('{}')
            (folder/'job.id').write_text('123\n')
            for accounting in ('123|FAILED|1:0|01:00|node\n', '123|RUNNING|0:0|01:00|node\n'):
                with patch.object(water.subprocess, 'check_output', return_value=accounting):
                    with self.assertRaisesRegex(ValueError, 'did not complete successfully'):
                        water.verify(folder)
            self.assertFalse((folder/'results/verified.json').exists())

    def test_host_limited_size_needs_full_science_and_observed_ram_limit(self):
        proof = dict(full_calibration=True, limiting_resource='host_memory_limit',
                     selected=dict(science_accepted=True, peak_gpu_fraction={'GPU-a': .55, 'GPU-b': .63}))
        self.assertTrue(campaign.host_limited_selection(proof, True))
        self.assertFalse(campaign.host_limited_selection(proof, False))
        for key, value in (('full_calibration', False), ('limiting_resource', 'gpu_memory_guard'), ('selected', None)):
            bad = {**proof, key: value}
            self.assertFalse(campaign.host_limited_selection(bad, True))
        bad = copy.deepcopy(proof); bad['selected']['science_accepted'] = False
        self.assertFalse(campaign.host_limited_selection(bad, True))
        for fraction in (.49, .76):
            bad = copy.deepcopy(proof); bad['selected']['peak_gpu_fraction']['GPU-a'] = fraction
            self.assertFalse(campaign.host_limited_selection(bad, True))


if __name__ == '__main__':
    unittest.main()
