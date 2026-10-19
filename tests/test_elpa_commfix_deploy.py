"""Exercise replacement, retained old libraries, and interrupted installation."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1]/'deployment/elpa-2026.02.001-commfix/install.py'
spec = importlib.util.spec_from_file_location('elpa_repair_install', SOURCE)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class ReplacementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prefix = self.root/'opt/nvidia'
        self.source = self.root/'payload'
        for tree, data in ((self.prefix, b'previous ELPA'), (self.source, b'repaired ELPA')):
            (tree/'lib').mkdir(parents=True)
            (tree/'lib/libelpa.so.19.5.1').write_bytes(data)
            (tree/'lib/libelpa.so.19.5.1').chmod(0o644)
            (tree/'lib/libelpa.so.19').symlink_to('libelpa.so.19.5.1')
        self.before = deploy.inventory(self.prefix)
        self.after = deploy.inventory(self.source)
        self.digest = 'a' * 64
        self.backup = self.prefix.with_name('.nvidia.before-'+self.digest[:16])

    def install(self):
        deploy.replace_one(self.prefix, self.source, self.before, self.after, self.digest)

    def test_replace_preserves_old_inode_and_is_idempotent(self):
        library = self.prefix/'lib/libelpa.so.19.5.1'
        inode = library.stat().st_ino
        with library.open('rb') as running_consumer:
            self.install()
            self.assertEqual(running_consumer.read(), b'previous ELPA')
        deploy.verify(self.prefix, self.after)
        deploy.verify(self.backup, self.before)
        self.assertEqual((self.backup/'lib/libelpa.so.19.5.1').stat().st_ino, inode)
        repaired_inode = library.stat().st_ino
        self.install()
        self.assertEqual(library.stat().st_ino, repaired_inode)

    def test_failed_switch_restores_previous_directory(self):
        rename = Path.rename
        def fail_stage(path, target):
            if '.commfix-install-' in path.name:
                raise OSError('simulated switch failure')
            return rename(path, target)
        with mock.patch.object(Path, 'rename', fail_stage):
            with self.assertRaisesRegex(OSError, 'switch failure'):
                self.install()
        deploy.verify(self.prefix, self.before)
        self.assertFalse(self.backup.exists())
        self.assertEqual(len(list(self.prefix.parent.glob('.nvidia.commfix-install-*'))), 1)

    def test_changed_install_or_payload_never_replaces_existing_library(self):
        for tree in (self.prefix, self.source):
            library = tree/'lib/libelpa.so.19.5.1'
            original = library.read_bytes()
            library.write_bytes(b'unreviewed change')
            with self.subTest(tree=tree), self.assertRaises(ValueError):
                self.install()
            self.assertFalse(self.backup.exists())
            library.write_bytes(original)

    def test_existing_backup_and_symlink_prefix_are_rejected(self):
        self.backup.mkdir()
        with self.assertRaisesRegex(ValueError, 'Backup already exists'):
            self.install()
        deploy.verify(self.prefix, self.before)
        alias = self.root/'alias'
        alias.symlink_to(self.prefix, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            deploy.replace_one(alias, self.source, self.before, self.after, self.digest)


if __name__ == '__main__':
    unittest.main()
