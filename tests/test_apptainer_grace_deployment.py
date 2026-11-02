import importlib.util
import json
from pathlib import Path
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
        self.here, self.prefix = root / 'handoff', root / 'opt/apptainer/1.4.4'
        self.target = self.prefix / 'libexec/apptainer/bin/starter'
        files = {self.target: b'original', self.prefix/'bin/apptainer': b'unchanged-cli',
                 self.here/'payload/starter': b'patched', self.here/'graceful-driver-stop.patch': b'patch'}
        for path, data in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o755)
        self.manifest = dict(site_starter_sha256=install.digest(self.target),
                             site_cli_sha256=install.digest(self.prefix/'bin/apptainer'),
                             starter_sha256=install.digest(self.here/'payload/starter'),
                             patch_sha256=install.digest(self.here/'graceful-driver-stop.patch'))
        (self.here/'manifest.json').write_text(json.dumps(self.manifest))
        self.proof = dict(verified=True, starter_sha256=self.manifest['starter_sha256'],
                          cases=[dict(label=label,data_verified=True,fsck_returncode=0,
                                      cleanup_failure=False,timeout_warning=False)
                                 for label in ('plain-small-a','plain-small-b','plain-build-size','delayed-fsync')])
        (self.here/'qualification.json').write_text(json.dumps(self.proof))

    def deploy(self, action):
        return install.deploy(action, self.here, self.prefix)

    def test_check_install_verify_repeat_and_rollback_preserve_cli(self):
        self.assertEqual(self.deploy('check')['normal_stop_grace_seconds'], 1)
        self.assertEqual(self.target.read_bytes(), b'original')
        self.assertTrue(self.deploy('install')['installed'])
        self.assertEqual(self.target.read_bytes(), b'patched')
        self.assertTrue(self.deploy('verify')['verified'])
        self.assertTrue(self.deploy('install')['installed'])
        self.assertTrue(self.deploy('rollback')['rolled_back'])
        self.assertTrue(self.deploy('rollback')['rolled_back'])
        self.assertEqual(self.target.read_bytes(), b'original')
        self.assertEqual((self.prefix/'bin/apptainer').read_bytes(), b'unchanged-cli')

    def test_rollback_uses_retained_backup_when_payload_is_gone(self):
        self.deploy('install')
        (self.here/'payload/starter').unlink()
        (self.here/'qualification.json').unlink()
        self.assertTrue(self.deploy('rollback')['rolled_back'])
        self.assertEqual(self.target.read_bytes(),b'original')

    def test_changed_original_or_payload_never_overwrites_runtime(self):
        for path in (self.target, self.here/'payload/starter'):
            original=path.read_bytes()
            path.write_bytes(b'different')
            target_before=self.target.read_bytes()
            with self.assertRaises(ValueError):self.deploy('install')
            self.assertEqual(self.target.read_bytes(),target_before)
            path.write_bytes(original)

    def test_failed_integrity_proof_blocks_installation(self):
        self.proof['cases'][0]['data_verified']=False
        (self.here/'qualification.json').write_text(json.dumps(self.proof))
        with self.assertRaisesRegex(ValueError,'qualification'):self.deploy('install')
        self.assertEqual(self.target.read_bytes(),b'original')

    def test_corrupt_atomic_copy_is_rejected_before_rename(self):
        def corrupt(src,dest):dest.write(b'corrupt-copy')
        with patch.object(install.shutil,'copyfileobj',side_effect=corrupt):
            with self.assertRaisesRegex(ValueError,'checksum'):self.deploy('install')
        self.assertEqual(self.target.read_bytes(),b'original')
        self.assertFalse(list(self.target.parent.glob('.starter-grace-*')))
