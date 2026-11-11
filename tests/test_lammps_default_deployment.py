"""Protect reviewed defaults from partial acceptance and unrelated site state."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'deployment/lammps-update1-default'
spec = importlib.util.spec_from_file_location('lammps_default_install', PACKAGE / 'install.py')
defaults = importlib.util.module_from_spec(spec)
spec.loader.exec_module(defaults)
sys.path.insert(0, str(ROOT / 'deployment/lammps-30Sep2026-update1'))
spec = importlib.util.spec_from_file_location('update1_help_install', ROOT / 'deployment/lammps-30Sep2026-update1/install.py')
update1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update1)
sys.path.pop(0)


class DefaultPublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.targets = self.root / 'modulefiles'
        self.targets.mkdir()
        self.content = {
            defaults.LAMMPS: b'new help\nruntime unchanged\n',
            defaults.NEPADAPTERS: b'compatible plugin module\n',
            'lammps/.modulerc.lua': b'module_version("lammps/update1", "default")\n',
            'nepadapters/.modulerc.lua': b'module_version("nepadapters/update1", "default")\n',
        }
        self.manifest = dict(modules={})
        import hashlib
        for name, content in self.content.items():
            self.manifest['modules'][name] = dict(sha256=hashlib.sha256(content).hexdigest())

    def file(self, name, content):
        path = self.targets / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content); path.chmod(0o644)
        return path

    def test_pending_matrix_cannot_publish(self):
        with patch.object(defaults, 'PACKAGE', self.root), self.assertRaisesRegex(ValueError, 'not yet packaged'):
            defaults.load_manifest()
        (self.root / 'manifest.json').write_text(json.dumps(dict(schema=1, ready=True, partitions=['16V100'])))
        with patch.object(defaults, 'PACKAGE', self.root), self.assertRaisesRegex(ValueError, 'incomplete'):
            defaults.load_manifest()
        self.assertEqual(list(self.targets.iterdir()), [])

    def test_existing_unrelated_default_prevents_any_new_module(self):
        path = self.file('lammps/.modulerc.lua', b'module_version("lammps/site", "default")\n')
        with patch.object(defaults, 'module_sources', return_value=self.content), self.assertRaisesRegex(ValueError, 'differs'):
            defaults.publish(self.manifest, self.targets)
        self.assertEqual(path.read_bytes(), b'module_version("lammps/site", "default")\n')
        self.assertFalse((self.targets / defaults.NEPADAPTERS).exists())

    def test_alternate_default_or_symlink_is_rejected(self):
        for name in ('lammps/.version', 'lammps/default'):
            path = self.file(name, b'existing')
            with self.assertRaisesRegex(ValueError, 'another default'):
                defaults.check_targets(self.manifest, self.targets)
            path.unlink()
        external = self.root / 'external'; external.mkdir()
        (self.targets / 'nepadapters').symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            defaults.check_targets(self.manifest, self.targets)

    def test_publication_is_repeatable_and_preserves_versioned_module(self):
        old = self.file('lammps/30Sep2026-deepmd320-auto', b'old version\n')
        with patch.object(defaults, 'module_sources', return_value=self.content):
            defaults.publish(self.manifest, self.targets)
            first = {name: (self.targets / name).stat().st_ino for name in self.content}
            defaults.publish(self.manifest, self.targets)
        self.assertEqual(first, {name: (self.targets / name).stat().st_ino for name in self.content})
        self.assertEqual(old.read_bytes(), b'old version\n')

    def test_help_migration_rejects_runtime_changes_even_with_known_hash(self):
        marker = 'if {[module-info mode help] || [module-info mode whatis]} {return}\n'
        old = self.root / 'old-module'; old.write_text('old help\n' + marker + 'runtime one\n')
        release = dict(previous_module_sha256=[defaults.digest(old)])
        with patch.object(update1, 'module_text', return_value='new help\n' + marker + 'runtime one\n'):
            self.assertTrue(update1.previous_module(release, old, Path('/')))
        with patch.object(update1, 'module_text', return_value='new help\n' + marker + 'runtime two\n'):
            self.assertFalse(update1.previous_module(release, old, Path('/')))


if __name__ == '__main__':
    unittest.main()
