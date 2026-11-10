"""Static documentation must follow the artifact, not merely its version label."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import build_info
from release_contract import make_identity


class BuildInfoTests(unittest.TestCase):
    def test_publication_preserves_full_output_and_rejects_wrong_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            identity = make_identity('lammps', 'development', 'develop', 'a'*40,
                                     'develop-20261110', 'b'*64, '16v100-avx2')
            row = dict(identity=identity, sha256='c'*64)
            value = dict(schema=1, identity=identity, image_sha256=row['sha256'],
                         catalog_sha256='d'*64, job='123',
                         records=[dict(command=['lmp', '-h'], returncode=0,
                                       output='Installed packages:\nGPU KOKKOS\n\nPair styles:\nnep/gpu/kk\n')])
            source = root / 'raw.json'
            source.write_text(json.dumps(value))
            reports = {'16V100': dict(job='123', catalog_sha256='d'*64,
                       build_info=dict(path=str(source), sha256=build_info.digest(source)))}
            snapshot = dict(applications={'lammps': dict(partitions={'16V100': row})})

            def write(path, text):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)

            files = build_info.publish(root, 'generation', 'lammps', snapshot, reports, write)
            record = root / 'build-info/generation/lammps/16V100.txt'
            self.assertIn(value['records'][0]['output'], record.read_text())
            self.assertIn('cat ' + str(record), record.with_name('index.txt').read_text())
            self.assertEqual(files[str(record.relative_to(root))], build_info.digest(record))
            with self.assertRaisesRegex(ValueError, 'another artifact'):
                build_info.verify(source, dict(row, sha256='e'*64), 'd'*64, '123')
            source.write_text(source.read_text() + ' ')
            with self.assertRaisesRegex(ValueError, 'changed after acceptance'):
                build_info.publish(root, 'another', 'lammps', snapshot, reports, write)

    def test_missing_partition_record_blocks_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'missing static'):
                build_info.publish(Path(tmp), 'generation', 'lammps',
                    {'applications': {'lammps': {'partitions': {'4V100': {}}}}},
                    {'4V100': {}}, lambda *args: None)

    def test_qe_and_gpumd_use_successful_run_instead_of_unsupported_help_flags(self):
        self.assertEqual(build_info.COMMANDS['qe'], [])
        self.assertEqual(build_info.COMMANDS['gpumd'], [])


if __name__ == '__main__':
    unittest.main()
