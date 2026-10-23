import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'controller'))
from deepmd_ci import locked_source, resolve_builds


class IndependentDeePMDCITests(unittest.TestCase):
    def source(self):
        return dict(software='deepmd-kit', repository='deepmodeling/deepmd-kit', track='development',
                    requested_ref='master', status='resolved', source_ref='master',
                    source_sha='5' * 40, source_version='master-2026-10-23')

    def test_resolve_once_fans_the_same_commit_to_requested_targets(self):
        calls = []
        def resolve(software, tracks):
            calls.append((software, tracks))
            return [self.source()]
        result = resolve_builds('development', '4v100-avx512,16v100-avx2,8v100v0-avx512', resolve)
        self.assertEqual(calls, [('deepmd-kit', ['development'])])
        self.assertEqual(len(result['builds']), 3)
        self.assertEqual({row['source_sha'] for row in result['builds']}, {'5' * 40})
        for row in result['builds']:
            self.assertEqual(locked_source(row), row)

    def test_channel_absence_is_reported_but_resolution_errors_propagate(self):
        missing = dict(software='deepmd-kit', track='prerelease', status='skipped',
                       reason='no latest-prerelease available')
        result = resolve_builds('prerelease', '16v100-avx2', lambda *_: [missing])
        self.assertEqual(result, dict(builds=[], skipped=[missing]))
        def failed(*_):
            raise RuntimeError('network failure')
        with self.assertRaisesRegex(RuntimeError, 'network'):
            resolve_builds('development', '16v100-avx2', failed)

    def test_invalid_matrix_source_cannot_fallback_to_a_moving_branch(self):
        source = dict(self.source(), target='16v100-avx2')
        for key, value in (('software', 'lammps'), ('repository', 'other/repo'),
                           ('status', 'skipped'), ('target', 'dsprhbm'), ('source_sha', 'master')):
            changed = copy.deepcopy(source)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                locked_source(changed)
        source.pop('source_sha')
        with self.assertRaises(ValueError):
            locked_source(source)

    def test_bad_or_duplicate_targets_fail_before_network_resolution(self):
        for targets in ('', 'dsprhbm', '16v100-avx2,16v100-avx2'):
            with self.subTest(targets=targets), self.assertRaises(ValueError):
                resolve_builds('development', targets, lambda *_: self.fail('network called'))


if __name__ == '__main__':
    unittest.main()
