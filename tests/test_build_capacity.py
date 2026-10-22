"""Run ownership remains exclusive; shared storage estimates never gate builds."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import build_capacity as capacity


class BuildCapacityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.first = self.root / 'runs/first'
        self.second = self.root / 'runs/second'
        self.first.mkdir(parents=True)
        self.second.mkdir()

    def test_parallel_runs_ignore_storage_estimates_and_legacy_reservations(self):
        (self.second / 'capacity.json').write_text(json.dumps(
            {'schema': 1, 'state': 'reserved', 'budget_bytes': 10**15}))
        with patch('os.statvfs', side_effect=AssertionError('must not query storage')), \
                patch.object(capacity.subprocess, 'check_output', side_effect=AssertionError('must not query jobs')), \
                redirect_stdout(io.StringIO()):
            with capacity.reserve(self.root, self.first, 32768):
                with capacity.reserve(self.root, self.second, 32768):
                    self.assertEqual(json.loads((self.second / 'capacity.json').read_text())[
                        'capacity_policy'], 'filesystem-enforced')

    def test_duplicate_run_is_rejected_and_exception_releases_ownership(self):
        with redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'child failed'):
                with capacity.reserve(self.root, self.first, 64):
                    with self.assertRaises(BlockingIOError):
                        with capacity.reserve(self.root, self.first, 64):
                            self.fail('duplicate run acquired')
                    raise RuntimeError('child failed')
            with capacity.reserve(self.root, self.first, 64):
                pass
        self.assertEqual(json.loads((self.first / 'capacity.json').read_text())['state'], 'released')

    def test_exited_process_does_not_leave_a_stale_lock(self):
        code = """
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
import build_capacity as capacity
with capacity.reserve(Path(sys.argv[2]), Path(sys.argv[3]), 64):
    input()
"""
        child = subprocess.Popen([sys.executable, '-u', '-c', code,
                                  str(Path(capacity.__file__).parent), str(self.root), str(self.first)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertIn('BUILD_CAPACITY_POLICY', child.stdout.readline())
            with self.assertRaises(BlockingIOError):
                with capacity.reserve(self.root, self.first, 64):
                    self.fail('live process lock ignored')
            child.terminate()
            child.communicate(timeout=10)
            with redirect_stdout(io.StringIO()), capacity.reserve(self.root, self.first, 64):
                pass
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=10)

    def test_redirected_run_or_lock_is_rejected(self):
        (self.second / 'capacity.json').symlink_to(self.first / 'capacity.json')
        with self.assertRaisesRegex(ValueError, 'redirected'):
            with capacity.reserve(self.root, self.second, 64):
                self.fail('redirected lock accepted')
        link = self.root / 'runs/alias'
        link.symlink_to(self.first)
        with self.assertRaisesRegex(ValueError, 'canonical'):
            with capacity.reserve(self.root, link, 64):
                self.fail('redirected run accepted')

    def test_child_storage_failure_is_reported_without_removing_artifacts(self):
        artifact = self.first / 'work.ext3'
        artifact.write_bytes(b'preserved checkpoint')
        result = subprocess.run([sys.executable, capacity.__file__, str(self.root), str(self.first),
            '16384', '--', sys.executable, '-c',
            'import errno; raise OSError(errno.ENOSPC, "No space left on device")'],
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('No space left on device', result.stderr)
        self.assertEqual(artifact.read_bytes(), b'preserved checkpoint')

    def run_script(self, script):
        return subprocess.run([sys.executable, capacity.__file__, '--shell-script-stdin',
            str(self.root), str(self.first), '16384'], input=script,
            capture_output=True, text=True, timeout=10)

    def test_stdin_reading_child_cannot_consume_export_and_verification(self):
        reader = shlex.join([sys.executable, '-c',
            'import sys; assert sys.stdin.read() == ""; print("PROBE_COMPLETE")'])
        script = reader + '\necho EXPORT_COMPLETE\necho VERIFY_COMPLETE\n'
        result = self.run_script(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-3:],
                         ['PROBE_COMPLETE', 'EXPORT_COMPLETE', 'VERIFY_COMPLETE'])

    def test_script_failure_still_stops_following_stages_and_releases_lock(self):
        for script in ('false | true\necho MUST_NOT_EXPORT\n',
                       'echo "$UNDEFINED_SAI_TEST_VARIABLE"\necho MUST_NOT_EXPORT\n'):
            with self.subTest(script=script):
                result = self.run_script(script)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('MUST_NOT_EXPORT', result.stdout)
                self.assertEqual(json.loads((self.first / 'capacity.json').read_text())['state'],
                                 'released')


if __name__ == '__main__':
    unittest.main()
