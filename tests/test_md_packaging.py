"""Real filesystem regression for fakeroot's lost read-only metadata modes."""
import json
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

from test_export_native import paired_entries
import export_native
import md_packaging


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'root'
        self.root.mkdir()
        self.entries = paired_entries()
        for entry in self.entries:
            prefix = self.root / entry['identity']['install_prefix'].lstrip('/')
            (prefix / 'bin').mkdir(parents=True)
            for command in entry['commands'].values():
                executable = prefix / command
                executable.write_bytes(b'#!/bin/sh\nexit 0\n')
                executable.chmod(0o755)
        self.expected = export_native.write_manifests(self.entries, self.root)
        self.identities = [entry['identity'] for entry in self.entries]
        self.manifests = [self.root / identity['install_prefix'].lstrip('/') / export_native.MANIFEST_PATH
                          for identity in self.identities]
        self.original = [path.read_bytes() for path in self.manifests]
        for row in self.expected['files']:
            if row['mode'] == 0o444:
                (self.root / row['path']).chmod(0o644)
        for path in self.manifests:
            path.chmod(0o644)

    def test_restores_modes_preserves_bytes_and_survives_squashfs(self):
        report = md_packaging.prepare(self.root, self.identities)
        self.assertEqual(report['payload_modes_restored'], 4)
        self.assertTrue(report['payload_bytes_unchanged'])
        self.assertEqual([path.read_bytes() for path in self.manifests], self.original)
        self.assertEqual(export_native.read_installed_manifests(self.entries, self.root), self.expected)
        self.assertEqual(export_native.inventory(self.entries, self.root), self.expected)
        if shutil.which('mksquashfs') and shutil.which('unsquashfs'):
            image = self.root.parent / 'packed.squashfs'
            subprocess.run(['mksquashfs', str(self.root), str(image), '-noappend', '-all-root',
                            '-no-xattrs', '-no-progress', '-processors', '1'],
                           check=True, capture_output=True)
            unpacked = self.root.parent / 'unpacked'
            subprocess.run(['unsquashfs', '-no-progress', '-d', str(unpacked), str(image)],
                           check=True, capture_output=True)
            self.assertEqual(export_native.read_installed_manifests(self.entries, unpacked), self.expected)
            self.assertEqual(export_native.inventory(self.entries, unpacked), self.expected)

    def test_rejects_content_change_before_chmod(self):
        executable = next(row for row in self.expected['files'] if row['path'].endswith('/bin/lmp'))
        (self.root / executable['path']).write_bytes(b'changed executable\n')
        with self.assertRaisesRegex(ValueError, 'content differs'):
            md_packaging.prepare(self.root, self.identities)
        self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o644 for path in self.manifests))

    def test_rejects_extra_files_and_changed_identity(self):
        extra = self.manifests[0].with_name('unexpected')
        extra.write_bytes(b'extra')
        with self.assertRaisesRegex(ValueError, 'file set differs'):
            md_packaging.prepare(self.root, self.identities)
        extra.unlink()
        changed = json.loads(self.original[0])
        changed['entry']['identity']['source_sha'] = 'f' * 40
        self.manifests[0].write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, 'identity differs'):
            md_packaging.prepare(self.root, self.identities)
