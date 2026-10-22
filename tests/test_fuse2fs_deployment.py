"""The public helper installer must preserve existing tools and exact bytes."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fuse2fs_public_install',
    ROOT / 'deployment/fuse2fs-45651efd/install.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class PublicInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.package = self.base / 'package'
        self.source = self.package / 'payload'
        self.prefix = self.base / 'tools/fuse2fs/version'
        self.module = self.base / 'modules/fuse2fs/version'
        self.prefix.parent.parent.mkdir(parents=True)
        self.module.parent.parent.mkdir(parents=True)
        files = {}
        for name, mode in deploy.MODES.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('qualified ' + name + '\n')
            path.chmod(mode)
            files[name] = dict(mode=mode, size=path.stat().st_size, sha256=deploy.digest(path))
        for directory in [self.source, *(p for p in self.source.rglob('*') if p.is_dir())]:
            directory.chmod(0o755)
        (self.package / 'modulefile').write_text('test module\n')
        self.release = dict(files=files, module_sha256=deploy.digest(self.package / 'modulefile'))
        for key, value in [('PACKAGE', self.package), ('PREFIX', self.prefix), ('MODULE', self.module)]:
            mocked = patch.object(deploy, key, value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_install_exact_payload_and_idempotent_repeat(self):
        deploy.install_files(self.release)
        deploy.verify_tree(self.prefix, self.release['files'])
        self.assertEqual(self.module.read_bytes(), (self.package / 'modulefile').read_bytes())
        deploy.install_files(self.release)
        self.assertEqual(list(self.prefix.parent.iterdir()), [self.prefix])

    def test_existing_module_conflict_prevents_application_publication(self):
        self.module.parent.mkdir()
        self.module.write_text('existing unrelated module')
        with self.assertRaisesRegex(ValueError, 'Refusing to replace'):
            deploy.install_files(self.release)
        self.assertFalse(self.prefix.exists())
        self.assertEqual(self.module.read_text(), 'existing unrelated module')

    def test_changed_existing_driver_is_never_overwritten(self):
        deploy.install_files(self.release)
        binary = self.prefix / 'bin/fuse2fs'
        binary.chmod(0o755)
        binary.write_text('different build')
        with self.assertRaisesRegex(ValueError, 'differs from release'):
            deploy.install_files(self.release)
        self.assertEqual(binary.read_text(), 'different build')

    def test_source_change_cannot_publish_prefix_or_module(self):
        source = self.source / 'etc/apptainer.conf'
        source.chmod(0o644)
        source.write_text('modified')
        with self.assertRaisesRegex(ValueError, 'Source changed'):
            deploy.install_files(self.release)
        self.assertFalse(self.prefix.exists())
        self.assertFalse(self.module.exists())

    def test_redirected_destination_parent_is_rejected(self):
        elsewhere = self.base / 'elsewhere'
        elsewhere.mkdir()
        self.prefix.parent.symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            deploy.install_files(self.release)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_unexpected_payload_files_are_rejected(self):
        (self.source / 'custom-test.py').write_text('unwanted public test')
        with self.assertRaisesRegex(ValueError, 'Unexpected/missing'):
            deploy.verify_tree(self.source, self.release['files'])

    def test_atomic_publication_cannot_replace_even_an_empty_destination(self):
        source, target = self.base / 'source', self.base / 'existing'
        source.mkdir()
        target.mkdir()
        with self.assertRaises(FileExistsError):
            deploy.rename_new(source, target)
        self.assertTrue(source.is_dir())
        self.assertTrue(target.is_dir())


if __name__ == '__main__':
    unittest.main()
