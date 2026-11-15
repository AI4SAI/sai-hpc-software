import importlib.util
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1] / 'deployment/deepmd-site-nccl-20261115'
SPEC = importlib.util.spec_from_file_location('deepmd_nccl_install', PACKAGE / 'install.py')
install = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install)


class NativeNcclInstallerTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.target = self.root / '3.2.0'
        self.original = (PACKAGE / 'original/3.2.0').read_bytes()
        self.candidate = (PACKAGE / 'modulefiles/deepmd-kit/3.2.0').read_bytes()
        self.target.write_bytes(self.original)
        self.target.chmod(0o644)
        self.library = self.root / 'libnccl.so.2'
        self.library.write_bytes(b'stand-in for the independently verified site binary')
        self.hash_patch = patch.object(install, 'NCCL_SHA256', install.digest(self.library.read_bytes()))
        self.hash_patch.start()
        self.addCleanup(self.hash_patch.stop)

    def check(self):
        return install.preflight(self.target, PACKAGE, self.library)

    def test_update_and_rollback_preserve_content_and_mode(self):
        original, candidate, before, info = self.check()
        expected_mode = stat.S_IMODE(info.st_mode)
        install.replace(self.target, before, candidate, info)
        self.assertEqual(self.target.read_bytes(), self.candidate)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), expected_mode)
        original, candidate, before, info = self.check()
        install.replace(self.target, before, original, info)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(list(self.root.glob('.3.2.0-nccl-*')), [])

    def test_newer_site_edit_is_preserved(self):
        changed = self.original + b'\n# subsequent administrator edit\n'
        self.target.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, 'newer edits'):
            self.check()
        self.assertEqual(self.target.read_bytes(), changed)

    def test_changed_library_is_rejected(self):
        self.library.write_bytes(b'different binary')
        with self.assertRaisesRegex(ValueError, 'tested binary'):
            self.check()
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_post_preflight_edit_is_preserved(self):
        original, candidate, before, info = self.check()
        changed = self.original + b'\n# edit after check\n'
        self.target.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, 'after preflight'):
            install.replace(self.target, before, candidate, info)
        self.assertEqual(self.target.read_bytes(), changed)

    def test_redirected_module_is_rejected(self):
        actual = self.root / 'actual-module'
        self.target.rename(actual)
        self.target.symlink_to(actual)
        with self.assertRaisesRegex(ValueError, 'redirected'):
            self.check()
        self.assertEqual(actual.read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main()
