"""The beta route uses the same model encoding already accepted in its SIF."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import beta_check
from export_native import MANIFEST_PATH, SquashfsReader
from sif_runtime import digest


@unittest.skipUnless(shutil.which('mksquashfs') and shutil.which('unsquashfs'), 'Squashfs tools required')
class GpumdBetaModelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.case = self.root / 'case'
        self.case.mkdir()
        self.legacy = b'nep4 fixed legacy model\n'
        self.modern = b'nep converted fixed model\n'
        (self.case / 'nep.txt').write_bytes(self.legacy)
        (self.case / 'gold.xyz').write_bytes(b'unchanged energy and forces\n')

    def image(self, modern=True, corrupt=False):
        identity = {'install_prefix': '/opt/apps/gpumd/qualified/16V100'}
        prefix = identity['install_prefix'].lstrip('/')
        payload = self.root / 'payload'
        models = {'nep.txt': self.legacy}
        if modern:
            models['nep-modern.txt'] = self.modern
        files = []
        for name, data in models.items():
            relative = prefix + '/share/sai/cases/static/' + name
            path = payload / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            files.append(dict(path=relative, kind='file', size=len(data),
                              sha256=hashlib.sha256(data).hexdigest()))
        if corrupt:
            files[-1]['sha256'] = '0' * 64
        (payload / prefix / MANIFEST_PATH).write_text(json.dumps(
            dict(schema=2, entry=dict(identity=identity), files=files)))
        image = self.root / 'image.squashfs'
        subprocess.run(['mksquashfs', str(payload), str(image), '-noappend', '-no-progress',
                        '-processors', '1'], check=True, stdout=subprocess.DEVNULL)
        self.reader = SquashfsReader(image, 0)
        return dict(image=str(image), sha256=digest(image), identity=identity)

    def prepare(self, row):
        with patch.object(SquashfsReader, 'from_sif', return_value=self.reader):
            return beta_check.prepare_gpumd_model(row, self.case)

    def test_modern_model_comes_from_image_and_keeps_gold_unchanged(self):
        row = self.image()
        gold = digest(self.case / 'gold.xyz')
        receipt = self.prepare(row)
        self.assertEqual((self.case / 'nep.txt').read_bytes(), self.modern)
        self.assertEqual(receipt['selected_sha256'], digest(self.case / 'nep.txt'))
        self.assertEqual(receipt['legacy_sha256'], hashlib.sha256(self.legacy).hexdigest())
        self.assertEqual(digest(self.case / 'gold.xyz'), gold)

    def test_legacy_image_keeps_legacy_model(self):
        receipt = self.prepare(self.image(modern=False))
        self.assertEqual((self.case / 'nep.txt').read_bytes(), self.legacy)
        self.assertEqual(receipt['legacy_sha256'], receipt['selected_sha256'])

    def test_another_fixture_or_build_cannot_select_converted_model(self):
        row = self.image()
        (self.case / 'nep.txt').write_bytes(b'another physical model\n')
        with self.assertRaisesRegex(ValueError, 'accepted legacy model'):
            self.prepare(row)
        (self.case / 'nep.txt').write_bytes(self.legacy)
        row['identity'] = dict(row['identity'], source_sha='another build')
        with self.assertRaisesRegex(ValueError, 'another build'):
            self.prepare(row)

    def test_image_or_converted_bytes_must_match_hashes(self):
        row = self.image(corrupt=True)
        with self.assertRaisesRegex(ValueError, 'converted model'):
            self.prepare(row)
        row['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'fixture image changed'):
            self.prepare(row)


if __name__ == '__main__':
    unittest.main()
