import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

DEPLOYMENT = Path(__file__).resolve().parents[1] / 'deployment/apptainer-1.4.4-sai'
spec = importlib.util.spec_from_file_location('apptainer_grace_install', DEPLOYMENT / 'install.py')
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.here = root / 'handoff'
        self.prefix = root / 'opt/apptainer-sai/1.4.4'
        self.module = root / 'modules/apptainer/1.4.4-sai.lua'
        self.module.parent.mkdir(parents=True)
        self.original = root / 'opt/apptainer/1.4.4/libexec/apptainer/bin/starter'
        self.original.parent.mkdir(parents=True)
        self.original.write_bytes(b'original')
        payload = root / 'source'
        files = {'bin/apptainer': b'unchanged-cli', 'libexec/apptainer/bin/starter': b'patched',
                 'etc/apptainer/apptainer.conf': b'config'}
        for name, data in files.items():
            p = payload / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            p.chmod(0o755)
        (payload / 'share').mkdir()
        (payload / 'bin/singularity').symlink_to('apptainer')
        (self.here / 'payload').mkdir(parents=True)
        self.entries = {}
        with tarfile.open(self.here / 'payload/apptainer-runtime.tar.gz', 'w:gz') as archive:
            for p in sorted(payload.rglob('*')):
                name = str(p.relative_to(payload))
                archive.add(p, arcname=name, recursive=False)
                if p.is_symlink():
                    entry = dict(type='symlink', target='apptainer')
                elif p.is_dir():
                    entry = dict(type='directory', mode=p.stat().st_mode & 0o7777)
                else:
                    entry = dict(type='file', mode=p.stat().st_mode & 0o7777, sha256=install.digest(p))
                self.entries[name] = entry
        (self.here / 'payload-files.json').write_text(json.dumps(self.entries))
        (self.here / 'module.lua').write_bytes(b'fixture module')
        (self.here / 'graceful-driver-stop.patch').write_bytes(b'patch')
        self.manifest = dict(site_cli_sha256=install.digest(payload / 'bin/apptainer'),
                             starter_sha256=install.digest(payload / 'libexec/apptainer/bin/starter'),
                             patch_sha256=install.digest(self.here / 'graceful-driver-stop.patch'),
                             payload_files_sha256=install.digest(self.here / 'payload-files.json'),
                             payload_sha256=install.digest(self.here / 'payload/apptainer-runtime.tar.gz'))
        (self.here / 'manifest.json').write_text(json.dumps(self.manifest))
        self.proof = dict(verified=True, starter_sha256=self.manifest['starter_sha256'],
                          binary_sha256=self.manifest['site_cli_sha256'],
                          cases=[dict(label=label, data_verified=True, fsck_returncode=0,
                                      cleanup_failure=False, timeout_warning=False)
                                 for label in ('plain-small-a', 'plain-small-b', 'plain-build-size', 'delayed-fsync')])
        (self.here / 'qualification.json').write_text(json.dumps(self.proof))

    def deploy(self, action, **kwargs):
        return install.deploy(action, self.here, self.prefix, self.module, **kwargs)

    def test_install_repeat_verify_preserve_original_and_wait_for_native_launch(self):
        self.assertFalse(self.deploy('check')['installed'])
        self.assertTrue(self.deploy('install')['installed'])
        self.assertTrue(self.deploy('install')['installed'])
        self.assertTrue(self.deploy('verify')['files_verified'])
        self.assertFalse(self.module.exists())
        self.assertEqual(self.original.read_bytes(), b'original')
        self.assertEqual((self.prefix / 'libexec/apptainer/bin/starter').read_bytes(), b'patched')
        self.assertEqual((self.prefix / 'bin/singularity').read_bytes(), b'unchanged-cli')

    def test_apparmor_failure_never_activates_module(self):
        self.deploy('install')
        with patch.object(install, 'runtime_check', side_effect=RuntimeError('AppArmor denied userns')):
            with self.assertRaisesRegex(RuntimeError, 'AppArmor'):
                self.deploy('activate', work=Path('/work'), image=Path('/image'))
        self.assertFalse(self.module.exists())
        self.assertEqual(self.original.read_bytes(), b'original')

    def test_activation_and_rollback_retain_both_runtimes(self):
        self.deploy('install')
        with patch.object(install, 'runtime_check', return_value={'returncode': 0}):
            self.assertTrue(self.deploy('activate', work=Path('/work'), image=Path('/image'))['module_active'])
        self.assertTrue(self.deploy('rollback')['rolled_back'])
        self.assertFalse(self.module.exists())
        self.assertTrue(self.deploy('verify')['files_verified'])
        self.assertEqual(self.original.read_bytes(), b'original')

    def test_read_only_preflight_and_failed_qualification_do_not_install(self):
        with patch.object(install.os, 'access', return_value=False):
            self.assertFalse(self.deploy('check')['installation_directory_writable'])
            with self.assertRaises(PermissionError):
                self.deploy('install')
        self.proof['cases'][0]['data_verified'] = False
        (self.here / 'qualification.json').write_text(json.dumps(self.proof))
        with self.assertRaisesRegex(ValueError, 'qualification'):
            self.deploy('install')
        self.assertFalse(self.prefix.exists())

    def test_corrupt_payload_or_partial_copy_never_publishes_prefix(self):
        archive = self.here / 'payload/apptainer-runtime.tar.gz'
        original = archive.read_bytes()
        archive.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.deploy('install')
        archive.write_bytes(original)
        with patch.object(install.shutil, 'copyfileobj', side_effect=OSError('copy failed')):
            with self.assertRaisesRegex(OSError, 'copy failed'):
                self.deploy('install')
        self.assertFalse(self.prefix.exists())
        self.assertFalse(list(self.prefix.parent.glob('.stage-*')))

    def test_unrelated_module_or_modified_runtime_is_preserved(self):
        self.module.write_bytes(b'other owner')
        with self.assertRaisesRegex(ValueError, 'unrelated module'):
            self.deploy('install')
        self.assertEqual(self.module.read_bytes(), b'other owner')
        self.module.unlink()
        self.deploy('install')
        target = self.prefix / 'libexec/apptainer/bin/starter'
        target.write_bytes(b'different upgrade')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.deploy('install')
        self.assertEqual(target.read_bytes(), b'different upgrade')

    def test_archive_traversal_and_external_links_are_rejected(self):
        archive = self.here / 'unsafe.tar.gz'
        destination = self.here / 'stage'
        destination.mkdir()
        for name, target in (('../escape', None), ('link', '/outside')):
            with tarfile.open(archive, 'w:gz') as bundle:
                member = tarfile.TarInfo(name)
                member.mode = 0o755
                if target:
                    member.type = tarfile.SYMTYPE
                    member.linkname = target
                    bundle.addfile(member)
                else:
                    member.size = 1
                    bundle.addfile(member, io.BytesIO(b'x'))
            with self.assertRaisesRegex(ValueError, 'unsafe|escapes'):
                install.extract_payload(archive, destination, {name: {'type': 'symlink' if target else 'file'}})
        self.assertEqual(list(destination.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
