import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import cp2k_devtools_application as app
from release_contract import make_identity


class ApplicationProbeTests(unittest.TestCase):
    def test_copy_is_independent_and_original_content_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original, clone = root / 'original.sif', root / 'copy.sif'
            original.write_bytes(b'accepted immutable dependencies')
            digest = app.checksum(original)
            before = original.stat()
            receipt = app.copy_image(original, clone, digest)
            self.assertFalse(os.path.samestat(before, clone.stat()))
            self.assertEqual(original.stat().st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(receipt['sha256'], digest)
            self.assertEqual(clone.stat().st_mode & 0o777, 0o400)
            clone.chmod(0o600)
            clone.write_bytes(b'changed independent copy')
            self.assertEqual(app.checksum(original), digest)
            with self.assertRaisesRegex(ValueError, 'already exists'):
                app.copy_image(original, clone, digest)

    def test_wrong_source_hash_stops_before_creating_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.sif'
            destination = Path(folder) / 'copy.sif'
            source.write_bytes(b'changed source')
            with self.assertRaisesRegex(ValueError, 'checksum changed'):
                app.copy_image(source, destination, '0' * 64)
            self.assertFalse(destination.exists())

    def test_changed_copy_is_recorded_and_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source.sif'
            source.write_bytes(b'accepted source')
            (root / 'results').mkdir()
            app.write_json(root / 'request.json', dict(source_sif=str(source), source_sif_sha256=app.checksum(source)))
            (root / 'dependencies.sif').write_bytes(b'changed copy')
            with self.assertRaisesRegex(ValueError, 'changed during application'):
                app.preserve(root, 0)
            receipt = json.loads((root / 'results/preservation.json').read_text())
            self.assertFalse(receipt['preserved'])
            self.assertFalse(receipt['published'])

    def test_probe_reads_copied_image_and_keeps_writes_in_new_overlay(self):
        project = Path('/org/org-open-sai/proj-sai-hpc-software')
        control, run = project / 'controller/probe', project / 'runs/probe'
        identity = make_identity('cp2k', 'release', 'v2026.2', 'a' * 40, 'v2026.2', 'b' * 64, '16v100-avx2')
        script = app.render(project, control, run, identity, node='16v100n43')
        subprocess.run(['bash', '-n'], input=script, text=True, check=True)
        self.assertIn(str(run / 'dependencies.sif'), script)
        self.assertIn(str(run / 'work.ext3') + ':ro', script)
        self.assertNotIn('/opt/devtools:/opt/devtools', script)
        self.assertNotIn('cp2k_spack_dependencies.sh', script)
        self.assertNotIn('libraries.sif', script)
        self.assertIn('/opt/devtools/gcc:/opt/devtools/gcc:ro', script)
        self.assertIn('#SBATCH --account=proj-sai-hpc-software', script)
        self.assertIn('trap finish EXIT', script)
        self.assertNotIn('rm ', script)


if __name__ == '__main__':
    unittest.main()
