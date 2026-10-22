"""Guard source repair against duplicate insertion and unrelated source edits."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import cp2k_source_fixes as fixes


class ELPASetupFixTests(unittest.TestCase):
    def test_repair_is_idempotent_and_records_exact_source_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source/src/fm/cp_fm_elpa.F'
            source.parent.mkdir(parents=True)
            original = 'SUBROUTINE test\n' + fixes.DECLARATION + fixes.ANCHOR + 'END SUBROUTINE\n'
            source.write_text(original)
            record = fixes.fix(root / 'source', root / 'metadata')
            self.assertTrue(record['applied'])
            self.assertEqual(record['before_sha256'], hashlib.sha256(original.encode()).hexdigest())
            changed = source.read_bytes()
            self.assertEqual(changed.count(b'elpa_obj%setup_gpu()'), 1)
            again = fixes.fix(root / 'source', root / 'metadata')
            self.assertFalse(again['applied'])
            self.assertEqual(source.read_bytes(), changed)

    def test_unknown_source_is_preserved_and_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source/src/fm/cp_fm_elpa.F'
            source.parent.mkdir(parents=True)
            source.write_text('unexpected changed source\n')
            with self.assertRaisesRegex(ValueError, 'unrecognized'):
                fixes.fix(root / 'source', root / 'metadata')
            self.assertEqual(source.read_text(), 'unexpected changed source\n')
            self.assertFalse((root / 'metadata').exists())
