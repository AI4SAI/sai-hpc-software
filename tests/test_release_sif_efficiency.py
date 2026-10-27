"""Reject incomparable paired measurements before reporting a container ratio."""
import copy
import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'deployment/release-sif-efficiency/run.py'
spec = importlib.util.spec_from_file_location('release_sif_efficiency', MODULE)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class PairedMeasurements(unittest.TestCase):
    def setUp(self):
        self.rows = [dict(case='science', repeat=str(i), route=route,
                          wall_seconds=duration + (route == 'sif'),
                          compute_seconds=duration, iterations=12)
                     for i, duration in enumerate((9, 10, 11)) for route in ('native', 'sif')]

    def test_excludes_calibration_and_warmup(self):
        ignored = [dict(self.rows[0], repeat=r, wall_seconds=10000)
                   for r in ('warmup', 'calibration0', 'calibration1')]
        result = benchmark.summarize(ignored + self.rows, 3)['science']
        self.assertEqual(result['wall_seconds']['paired_delta_median'], 1)
        self.assertEqual(result['compute_seconds']['ratio'], 1)
        self.assertAlmostEqual(result['wall_seconds']['percent_change'], 10)

    def test_rejects_missing_arm(self):
        with self.assertRaises(AssertionError):
            benchmark.summarize(self.rows[:-1], 3)

    def test_rejects_duplicate_pair_even_with_correct_sample_count(self):
        rows = copy.deepcopy(self.rows)
        rows[-1]['repeat'] = '0'
        with self.assertRaises(AssertionError):
            benchmark.summarize(rows, 3)

    def test_rejects_different_workload(self):
        for key in ('iterations', 'steps', 'evaluations', 'atoms'):
            with self.subTest(key=key):
                rows = [dict(r, **{key: 12}) for r in self.rows]
                rows[-1][key] = 13
                with self.assertRaises(AssertionError):
                    benchmark.summarize(rows, 3)

    def test_rejects_nonpositive_and_nonfinite_durations(self):
        for invalid in (0, -1, float('nan'), float('inf')):
            with self.subTest(invalid=invalid):
                rows = copy.deepcopy(self.rows)
                rows[-1]['wall_seconds'] = invalid
                with self.assertRaises(AssertionError):
                    benchmark.summarize(rows, 3)

    def test_alternates_both_launch_orders(self):
        self.assertEqual(list(benchmark.schedule(3)), [
            ('warmup', 'native'), ('warmup', 'sif'), ('0', 'native'), ('0', 'sif'),
            ('1', 'sif'), ('1', 'native'), ('2', 'native'), ('2', 'sif')])


if __name__ == '__main__':
    unittest.main()
