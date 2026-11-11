"""Protect the GPUMDkit administrator export against unsafe paths and modes."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('gpumdkit_install',
    Path(__file__).resolve().parents[1] / 'deployment/gpumdkit-1.5.9/install.py')
install = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install)


class DeploymentTests(unittest.TestCase):
    def test_public_directories_override_private_administrator_umask(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'modules/apps/gpumdkit'
            previous = os.umask(0o077)
            try:
                install.directories(target)
            finally:
                os.umask(previous)
            for path in (target, target.parent, target.parent.parent):
                self.assertEqual(path.stat().st_mode & 0o777, 0o755)

    def test_rejects_privileged_modes_and_links_outside_the_inventory(self):
        root = dict(path='.', kind='directory', mode=0o755)
        regular = dict(path='python', kind='file', mode=0o755)
        link = dict(path='python3', kind='symlink', mode=0o777, target='python')
        install.validate_inventory([root, regular, link])
        for changed in (dict(link, target='../outside'), dict(link, target='/etc/passwd'),
                        dict(link, target='missing'), dict(link, target='python3'),
                        dict(regular, mode=0o4755), dict(regular, path='./python')):
            records = [root, regular, changed] if changed['kind'] == 'symlink' else [root, changed]
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                install.validate_inventory(records)

    def test_symlink_ancestor_does_not_create_outside_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'outside').mkdir()
            (root / 'redirect').symlink_to(root / 'outside')
            with self.assertRaisesRegex(ValueError, 'symlink'):
                install.directories(root / 'redirect/new')
            self.assertFalse((root / 'outside/new').exists())


if __name__ == '__main__':
    unittest.main()
