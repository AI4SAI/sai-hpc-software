"""Catch the observed resource leak even when all numerical solves pass."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('elpa_lifecycle', ROOT/'deployment/elpa-2026.02.001/verify-lifecycle.py')
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)


class LifecycleTests(unittest.TestCase):
    def log(self, ranks=2, gpu=1, objects=32):
        lines = [f'ELPA_NUMERIC PASS ranks={ranks} gpu={gpu} solver=2 complex=0 nonblocking=0 '
                 f'repeat={i % 2} spectrum=2e-15 residual=3e-16 orthogonality=4e-16'
                 for i in range(objects * 2)]
        count = objects if gpu else 0
        return '\n'.join(lines + [f'ELPA_COMM_TRACE created={count} explicitly_freed={count} live_at_exit=0'] * ranks)

    def test_cpu_and_gpu_repeated_objects_on_both_ranks(self):
        for ranks in (1, 2):
            for gpu in (0, 1):
                report = lifecycle.validate(self.log(ranks, gpu), ranks, gpu, 32)
                self.assertEqual(report['numerical_solves'], 64)
                self.assertEqual(report['live_per_rank'], 0)

    def test_observed_unpatched_gpu_log_is_rejected(self):
        log = (ROOT/'docs/evidence/cp2k-shared-elpa-20261019/communicator-audit/work/gpu-1.log').read_text()
        with self.assertRaisesRegex(ValueError, 'Communicator lifetime failed'):
            lifecycle.validate(log, 1, 1, 8)

    def test_missing_instrumentation_or_one_rank_leak_is_rejected(self):
        good = self.log()
        for log in (good.replace('ELPA_COMM_TRACE', 'untraced'),
                    good.rsplit('\n', 1)[0],
                    good.replace('explicitly_freed=32 live_at_exit=0', 'explicitly_freed=31 live_at_exit=1', 1),
                    good.replace('created=32 explicitly_freed=32', 'created=0 explicitly_freed=0')):
            with self.subTest(log=log[-160:]), self.assertRaises(ValueError):
                lifecycle.validate(log, 2, 1, 32)

    def test_bad_numeric_results_cannot_pass_resource_checks(self):
        good = self.log()
        for log in (good.replace('PASS', 'FAIL', 1), good.replace('residual=3e-16', 'residual=nan', 1),
                    good.replace('ranks=2', 'ranks=1', 1), good.split('\n', 1)[1]):
            with self.subTest(log=log[:160]), self.assertRaises(ValueError):
                lifecycle.validate(log, 2, 1, 32)


if __name__ == '__main__':
    unittest.main()
