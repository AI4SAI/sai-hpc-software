"""Concurrent allocations reserve growth and release their locks after exit."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
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
        disk = SimpleNamespace(f_bavail=capacity.RESERVE_BYTES + 100 * 2**20, f_frsize=1)
        self.disk_patch = patch.object(capacity.os, 'statvfs', return_value=disk)
        self.disk_patch.start()
        self.addCleanup(self.disk_patch.stop)
        capture = redirect_stdout(io.StringIO())
        capture.__enter__()
        self.addCleanup(capture.__exit__, None, None, None)

    def test_second_allocation_cannot_overcommit_first_and_exception_releases(self):
        with capacity.reserve(self.root, self.first, 64, timeout=0):
            with self.assertRaises(TimeoutError):
                with capacity.reserve(self.root, self.second, 64, timeout=0):
                    self.fail('overcommitted')
            with capacity.reserve(self.root, self.second, 32, timeout=0):
                pass
        with self.assertRaisesRegex(RuntimeError, 'child failed'):
            with capacity.reserve(self.root, self.second, 64, timeout=0):
                raise RuntimeError('child failed')
        with capacity.reserve(self.root, self.first, 64, timeout=0):
            pass

    def test_allocated_blocks_are_not_reserved_twice(self):
        with capacity.reserve(self.root, self.first, 64, timeout=0):
            image = self.first / 'work.ext3'
            with image.open('wb') as stream:
                stream.write(b'x' * 4 * 2**20)
            self.assertGreaterEqual(capacity.allocated(self.first), 4 * 2**20)
            self.assertLessEqual(capacity.pending_growth(self.root, self.second / 'capacity.json'), 60 * 2**20)

    def test_exited_process_does_not_leave_a_stale_reservation(self):
        code = """
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import build_capacity as capacity
with patch.object(capacity.os, 'statvfs', return_value=SimpleNamespace(f_bavail=10**12, f_frsize=1)):
    with capacity.reserve(Path(sys.argv[2]), Path(sys.argv[3]), 64, timeout=0):
        input()
"""
        child = subprocess.Popen([sys.executable, '-u', '-c', code,
                                  str(Path(capacity.__file__).parent), str(self.root), str(self.first)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertIn('BUILD_CAPACITY_RESERVED', child.stdout.readline())
            with self.assertRaises(TimeoutError):
                with capacity.reserve(self.root, self.second, 64, timeout=0):
                    self.fail('live process reservation ignored')
            child.terminate()
            child.communicate(timeout=10)
            self.assertEqual(json.loads((self.first / 'capacity.json').read_text())['state'], 'reserved')
            with capacity.reserve(self.root, self.second, 64, timeout=0):
                pass
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=10)

    def test_duplicate_run_and_redirected_lock_are_rejected(self):
        with capacity.reserve(self.root, self.first, 64, timeout=0):
            with self.assertRaises(BlockingIOError):
                with capacity.reserve(self.root, self.first, 1, timeout=0):
                    self.fail('duplicate run acquired')
        (self.second / 'capacity.json').symlink_to(self.first / 'capacity.json')
        with self.assertRaisesRegex(ValueError, 'redirected'):
            with capacity.reserve(self.root, self.second, 1, timeout=0):
                pass

    def test_running_legacy_dependency_is_budgeted_without_changing_it(self):
        (self.first / 'job.id').write_text('123\n')
        request = self.first / 'request.json'
        request.write_text(json.dumps({'overlay_mb': 64,
                                      'purpose': 'cp2k-final-prefix-dependencies-not-application-acceptance'}))
        original = request.read_bytes()
        with patch.object(capacity.subprocess, 'check_output', return_value='123\n'):
            with self.assertRaises(TimeoutError):
                with capacity.reserve(self.root, self.second, 64, timeout=0):
                    self.fail('legacy allocation ignored')
        self.assertEqual(request.read_bytes(), original)
        with patch.object(capacity.subprocess, 'check_output', return_value=''):
            with capacity.reserve(self.root, self.second, 64, timeout=0):
                pass


if __name__ == '__main__':
    unittest.main()
