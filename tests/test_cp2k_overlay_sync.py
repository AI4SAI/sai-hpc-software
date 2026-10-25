import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
from cp2k_overlay_sync import synchronize


class OverlaySyncTests(unittest.TestCase):
    def test_success_records_completed_sync_without_removing_the_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = synchronize('build', root, root)
            self.assertEqual((root / '.sai-overlay-sync').read_text(), 'build\n')
            self.assertEqual(json.loads((root / 'overlay-sync-build.json').read_text()), record)
            self.assertTrue(record['synchronized'])

    def test_sync_error_cannot_record_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch('cp2k_overlay_sync.os.fsync', side_effect=OSError('backing image I/O failure')):
                with self.assertRaisesRegex(OSError, 'I/O failure'):
                    synchronize('dependencies', root, root)
            self.assertFalse((root / 'overlay-sync-dependencies.json').exists())

    def test_readonly_phase_does_not_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ValueError):
                synchronize('verify', root, root)
            self.assertEqual(list(root.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
