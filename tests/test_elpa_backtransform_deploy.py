"""Exercise the NVIDIA-only handoff and its preflight-before-mutation guarantee."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / 'deployment/elpa-2026.02.001-backtransform/install.py'
spec = importlib.util.spec_from_file_location('elpa_backtransform_install', SOURCE)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class HandoffTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.package = self.root / 'package'
        self.package.mkdir()
        self.module = self.root / 'public-module'
        self.module.write_text('accepted simple selector\n')
        self.prefixes = {isa: self.root / 'opt' / isa for isa in ('avx2', 'avx512', 'dsprhbm')}
        self.release = dict(revision='backtransform-host-staging-1', builds={},
            base_installer_sha256=deploy.base.checksum(deploy.base_path),
            module_sha256=deploy.base.checksum(self.module), backtransform_patch_sha256=None)
        for isa, prefix in self.prefixes.items():
            (prefix / 'lib').mkdir(parents=True)
            (prefix / 'lib/elpa.so').write_text('old ' + isa)
            if isa == 'dsprhbm':
                continue
            payload = self.package / 'payload' / isa
            (payload / 'lib').mkdir(parents=True)
            (payload / 'lib/elpa.so').write_text('new ' + isa)
            (payload / 'share/sai').mkdir(parents=True)
            patch = payload / 'share/sai/gpu-backtransform-host-staging.patch'
            patch.write_text('reviewed candidate patch\n')
            self.release['backtransform_patch_sha256'] = deploy.base.checksum(patch)
            build = dict(prefix=str(prefix))
            for tree, filename, key in [(prefix, isa + '-previous-manifest.json', 'previous_manifest_sha256'),
                                        (payload, isa + '-manifest.json', 'manifest_sha256')]:
                path = self.package / filename
                path.write_text(json.dumps(deploy.base.inventory(tree)))
                build[key] = deploy.base.checksum(path)
            self.release['builds'][isa] = build
        (self.package / 'release.json').write_text(json.dumps(self.release))
        self.enterContext(mock.patch.object(deploy, 'PACKAGE', self.package))
        self.enterContext(mock.patch.multiple(deploy.base, MODULE=self.module, PREFIXES=self.prefixes))

    def run_mode(self, mode):
        with mock.patch.object(sys, 'argv', ['install.py', mode]):
            deploy.main()

    def test_installs_only_gpu_prefixes_and_preserves_module_cpu_and_old_readers(self):
        cpu = deploy.base.inventory(self.prefixes['dsprhbm'])
        module_inode = self.module.stat().st_ino
        with (self.prefixes['avx2'] / 'lib/elpa.so').open() as reader:
            self.run_mode('--install')
            self.assertEqual(reader.read(), 'old avx2')
        self.run_mode('--verify')
        self.run_mode('--install')
        self.assertEqual(deploy.base.inventory(self.prefixes['dsprhbm']), cpu)
        self.assertEqual(self.module.stat().st_ino, module_inode)
        for isa in ('avx2', 'avx512'):
            digest = self.release['builds'][isa]['previous_manifest_sha256'][:16]
            backup = self.prefixes[isa].with_name('.' + isa + '.before-' + digest)
            self.assertEqual((backup / 'lib/elpa.so').read_text(), 'old ' + isa)

    def test_changed_second_payload_prevents_both_replacements(self):
        (self.package / 'payload/avx512/lib/elpa.so').write_text('unreviewed library')
        with self.assertRaisesRegex(ValueError, 'Payload differs'):
            self.run_mode('--install')
        for isa in ('avx2', 'avx512'):
            self.assertEqual((self.prefixes[isa] / 'lib/elpa.so').read_text(), 'old ' + isa)

    def test_public_module_change_stops_preflight(self):
        self.module.write_text('administrator change\n')
        with self.assertRaisesRegex(ValueError, 'Public module changed'):
            self.run_mode('--install')
        self.assertEqual((self.prefixes['avx2'] / 'lib/elpa.so').read_text(), 'old avx2')


if __name__ == '__main__':
    unittest.main()
