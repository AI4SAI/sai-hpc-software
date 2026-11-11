"""Exercise runtime pruning and permissions before the administrator exports SIFs."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1] / 'deployment/neptrainkit-3.1.5'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = load('neptrainkit_prepare', PACKAGE / 'prepare.py')
with patch.dict('sys.modules', {'prepare': prepare}):
    install = load('neptrainkit_install', PACKAGE / 'install.py')


class NativeProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_umask077_creates_public_ancestors(self):
        previous = os.umask(0o077)
        try:
            target = self.root / 'opt/apps/neptrainkit/3.1.5'
            install.directory(target)
        finally:
            os.umask(previous)
        for name in ('opt', 'opt/apps', 'opt/apps/neptrainkit', 'opt/apps/neptrainkit/3.1.5'):
            self.assertEqual((self.root / name).stat().st_mode & 0o777, 0o755)

    def test_pruning_preserves_application_data_and_library_links(self):
        prefix = self.root / 'payload'
        paths = ['bin/nepkit', 'lib/python3.12/site-packages/NepTrainKit/Config/nep89.txt',
                 'lib/python3.12/site-packages/numpy/tests/test_fixture.py',
                 'lib/python3.12/site-packages/NepTrainKit/__pycache__/main.pyc',
                 'lib/libexample.so.1']
        for name in paths:
            path = prefix / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
            path.chmod(0o775 if name.startswith('bin/') else 0o664)
        (prefix / 'lib/libexample.so').symlink_to('libexample.so.1')
        expected = install.inventory(prefix, projected=True)
        install.normalize(prefix)
        self.assertEqual(install.inventory(prefix), expected)
        self.assertTrue((prefix / paths[1]).is_file())
        self.assertFalse((prefix / paths[2]).exists())
        self.assertFalse((prefix / paths[3]).exists())
        self.assertEqual((prefix / 'lib/libexample.so').read_text(), 'lib/libexample.so.1')

    def test_link_into_removed_test_tree_is_rejected(self):
        prefix = self.root / 'payload'
        (prefix / 'lib/python3.12/test').mkdir(parents=True)
        (prefix / 'lib/python3.12/test/test_data').write_text('fixture')
        (prefix / 'required-data').symlink_to('lib/python3.12/test/test_data')
        with self.assertRaisesRegex(ValueError, 'missing/omitted'):
            install.inventory(prefix, projected=True)


if __name__ == '__main__':
    unittest.main()
