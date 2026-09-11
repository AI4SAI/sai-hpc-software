import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
from md_performance import ATOMS, calibrate, parse_engine_time, render_input, verify_performance


def output(steps, seconds):
    return f'Loop time of {seconds} on 2 procs for {steps} steps with {ATOMS} atoms\n'


class PerformanceTests(unittest.TestCase):
    def frozen(self):
        return calibrate(lambda steps, text: output(steps, steps / 100), 'tf', 'a' * 64, 'node1', {'ranks': 2})

    def records(self, frozen):
        return [dict(implementation=name, warmup=index == 0, node='node1', resources={'ranks': 2},
                     input_sha256=frozen['input_sha256'], stdout=output(frozen['steps'], seconds), wall_seconds=seconds + 2,
                     scientific_verified=True, execution_device={'kind': 'cpu', 'backend': 'tf',
                     'device_verified': True, 'evidence': 'validated CPU backend execution log'})
                for name, seconds in [('baseline', 6), ('candidate', 4)] for index in range(4)]

    def test_representative_input_and_engine_not_startup_timing(self):
        text = render_input('tf', 1000)
        self.assertIn('replicate 7 7 7', text)
        self.assertIn('run 1 post no\nrun 1000 pre no', text)
        self.assertIn('fix sai_plumed', text)
        frozen = self.frozen()
        self.assertGreaterEqual(frozen['calibration'][-1]['engine_seconds'], 5)
        self.assertGreater(frozen['steps'], 100)
        result = verify_performance(self.records(frozen), frozen)
        self.assertEqual(result['speedup'], 1.5)
        self.assertEqual(result['candidate']['median_wall_seconds'], 6)

    def test_partial_or_wrong_loop_output_rejected(self):
        for text in ['', output(100, 1) * 2, output(99, 4), output(100, 'nan'), output(100, -1),
                     output(100, 4).replace('2058 atoms', '6 atoms')]:
            with self.assertRaises(ValueError):
                parse_engine_time(text, 100)

    def test_fairness_device_science_interval_and_identity_gates(self):
        frozen = self.frozen()
        for change in [dict(node='other'), dict(resources={'ranks': 1}), dict(input_sha256='b' * 64),
                       dict(profiled=True),
                       dict(scientific_verified=False), dict(wall_seconds=0.1),
                       dict(stdout=output(frozen['steps'], 0.5)),
                       dict(execution_device={'kind': 'gpu', 'backend': 'tf', 'device_verified': True, 'evidence': 'trace'})]:
            rows = self.records(frozen)
            rows[-1].update(change)
            with self.assertRaises(ValueError):
                verify_performance(rows, frozen)
        with self.assertRaises(ValueError):
            verify_performance(self.records(frozen)[:-1], frozen)

    def test_frozen_step_or_input_change_rejected(self):
        frozen = self.frozen()
        records = self.records(frozen)
        frozen['steps'] += 1
        with self.assertRaises(ValueError):
            verify_performance(records, frozen)

    def test_calibration_rejects_too_short_interval_and_excessive_work(self):
        with self.assertRaises(ValueError):
            calibrate(lambda steps, text: output(steps, 1e-12), 'tf', 'a' * 64, 'node1', {'ranks': 2})
        with self.assertRaises(ValueError):
            calibrate(lambda steps, text: output(steps, 10), 'tf', 'a' * 64, 'node1', {'ranks': 2}, minimum=0.1)


if __name__ == '__main__':
    unittest.main()
