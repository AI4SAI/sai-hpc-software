"""Regressions observed with the real LAMMPS development SIF."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('lammps_independent_acceptance',
    ROOT / 'docs/evidence/lammps-independent-20261022/acceptance.py')
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)


class HelpVersionTests(unittest.TestCase):
    def test_release_and_development_c_api_versions_differ_at_month_boundary(self):
        banner = 'Large-scale Atomic/Molecular Massively Parallel Simulator - 30 Sep 2026'
        self.assertEqual(acceptance.help_version(banner + '\n'), '20260930')
        self.assertEqual(acceptance.help_version(banner + ' - Development\n'), '20260931')
        self.assertEqual(acceptance.help_version(banner + ' - Maintenance - stable_30Sep2026-1-g123\n'),
                         '20260931')

    def test_missing_or_duplicated_help_is_not_a_version_confirmation(self):
        banner = 'Large-scale Atomic/Molecular Massively Parallel Simulator - 30 Sep 2026\n'
        for text in ('LAMMPS (30 Sep 2026)\n', banner * 2):
            with self.subTest(text=text), self.assertRaises(ValueError):
                acceptance.help_version(text)
