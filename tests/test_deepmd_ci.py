import copy
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'controller'))
from deepmd_ci import locked_source, query_status, resolve_builds


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

    def test_dropped_status_query_retries_same_run_without_submission(self):
        unavailable = subprocess.CompletedProcess([], 255, '', 'connection reset')
        completed = subprocess.CompletedProcess([], 0, '{"scientific_verified":true}', '')
        remote = Mock(side_effect=[unavailable, unavailable, completed])
        with patch('deepmd_ci.time.sleep') as sleep:
            self.assertIs(query_status(remote, 'dp-existing'), completed)
        self.assertEqual(remote.call_count, 3)
        for call in remote.call_args_list:
            self.assertEqual(call.args, ('deepmd_standalone.py', 'status', 'dp-existing'))
            self.assertEqual(call.kwargs, dict(capture_output=True, check=False))
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [5, 10])

    def test_status_retries_are_bounded_and_preserve_controller_results(self):
        unavailable = subprocess.CompletedProcess([], 255, '', 'connection reset')
        remote = Mock(return_value=unavailable)
        with patch('deepmd_ci.time.sleep'):
            self.assertIs(query_status(remote, 'dp-existing'), unavailable)
        self.assertEqual(remote.call_count, 3)
        for code in (0, 1, 2, 3):
            response = subprocess.CompletedProcess([], code, '{}', 'controller diagnostic')
            remote = Mock(return_value=response)
            with self.subTest(code=code), patch('deepmd_ci.time.sleep') as sleep:
                self.assertIs(query_status(remote, 'dp-existing'), response)
                remote.assert_called_once()
                sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
