import importlib.util
from pathlib import Path
import unittest


source = Path(__file__).resolve().parents[1] / 'deployment/self-hosted-relay/check_capacity.py'
spec = importlib.util.spec_from_file_location('relay_capacity', source)
capacity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capacity)


class CapacityTests(unittest.TestCase):
    def check(self, available_mib, pressure=0):
        memory = f'MemTotal: 469000 kB\nMemAvailable: {available_mib * 1024} kB\n'
        return capacity.refusal(memory, f'some avg10={pressure} avg60=0.0 total=0\n', 288 * 1024)

    def test_observed_do2_headroom_cannot_admit_second_runner(self):
        self.assertIn('need 288 MiB', self.check(210))

    def test_capacity_boundary_includes_host_reserve(self):
        self.assertIsNotNone(self.check(287))
        self.assertIsNone(self.check(288))

    def test_high_pressure_blocks_even_with_available_memory(self):
        self.assertIn('memory pressure', self.check(320, 88.94))

    def test_memfree_is_not_a_fallback_for_missing_memavailable(self):
        with self.assertRaises(KeyError):
            capacity.refusal('MemFree: 400000 kB\n', 'some avg10=0\n', 288 * 1024)

    def test_invalid_pressure_never_admits_runner(self):
        self.assertIsNotNone(self.check(320, 'nan'))


if __name__ == '__main__':
    unittest.main()
