"""Protect the GPUMDkit administrator export against unsafe paths and modes."""
import importlib.util
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('gpumdkit_install',
    Path(__file__).resolve().parents[1] / 'deployment/gpumdkit-1.5.9/install.py')
install = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install)


class DeploymentTests(unittest.TestCase):
    def payload_fixture(self, root):
        root.mkdir(parents=True)
        (root / 'data').write_bytes(b'data')
        (root / 'tool').write_bytes(b'#!/bin/sh\n')
        (root / 'alias').symlink_to('tool')
        records = [dict(path='.', kind='directory', mode=0o755),
                   dict(path='alias', kind='symlink', mode=0o777, target='tool')]
        for name, mode in [('data', 0o664), ('tool', 0o775)]:
            path = root / name
            records.append(dict(path=name, kind='file', mode=mode, size=path.stat().st_size,
                                sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        return records

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

    def test_native_modes_ignore_extractor_permissions_and_preserve_payload(self):
        for data_mode, tool_mode in [(0o664, 0o775), (0o644, 0o755), (0o600, 0o700)]:
            with self.subTest(data_mode=data_mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / 'payload'
                archived = self.payload_fixture(root)
                root.chmod(0o700)
                (root / 'data').chmod(data_mode)
                (root / 'tool').chmod(tool_mode)
                records = install.native_inventory(archived)
                install.normalize_staged_permissions(root, records)
                self.assertEqual(root.stat().st_mode & 0o7777, 0o755)
                self.assertEqual((root / 'data').stat().st_mode & 0o7777, 0o644)
                self.assertEqual((root / 'tool').stat().st_mode & 0o7777, 0o755)
                self.assertEqual(os.readlink(root / 'alias'), 'tool')
                self.assertEqual(archived[-1]['mode'], 0o775)
                install.verify(root, records)
                (root / 'data').chmod(0o664)
                with self.assertRaisesRegex(ValueError, 'changed mode'):
                    install.verify(root, records)

    def test_normalization_rejects_changed_content_before_chmod(self):
        for corruption in ('content', 'symlink', 'extra'):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / 'payload'
                records = install.native_inventory(self.payload_fixture(root))
                (root / 'tool').chmod(0o700)
                if corruption == 'content':
                    (root / 'data').write_bytes(b'evil')
                elif corruption == 'symlink':
                    (root / 'alias').unlink()
                    (root / 'alias').symlink_to('../outside')
                else:
                    (root / 'unexpected').touch()
                with self.assertRaises(ValueError):
                    install.normalize_staged_permissions(root, records)
                self.assertEqual((root / 'tool').stat().st_mode & 0o777, 0o700)

    def test_resume_is_limited_to_owned_complete_sibling_extractions(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / '1.5.9'
            stage = target.with_name('.1.5.9-incoming-123')
            payload = stage / install.PREFIX.relative_to('/')
            records = install.native_inventory(self.payload_fixture(payload))
            self.assertEqual(install.resumed_payload(stage, target), payload)
            install.normalize_staged_permissions(payload, records)
            with patch.object(install.os, 'geteuid', return_value=os.geteuid() + 1):
                with self.assertRaisesRegex(ValueError, 'owned'):
                    install.resumed_payload(stage, target)
            (stage / 'unrelated').touch()
            with self.assertRaisesRegex(ValueError, 'unexpected entries'):
                install.resumed_payload(stage, target)
            (stage / 'unrelated').unlink()
            redirect = target.with_name('.1.5.9-incoming-456')
            redirect.symlink_to(stage)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                install.resumed_payload(redirect, target)
            with self.assertRaisesRegex(ValueError, 'sibling'):
                install.resumed_payload(stage, Path(temporary) / 'other/1.5.9')


if __name__ == '__main__':
    unittest.main()
