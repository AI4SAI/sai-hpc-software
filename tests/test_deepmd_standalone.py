import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'controller'))
import deepmd_standalone as standalone
from deepmd_standalone_check import inventory, verify_science_files


class IndependentDeePMDTests(unittest.TestCase):
    def delivery(self, target='16v100-avx2'):
        return standalone.make_delivery(dict(track='development', source_ref='master',
            source_sha='5' * 40, source_version='master-2026-10-23'), target)

    def test_only_deepmd_identity_is_locked_and_recipe_drift_is_rejected(self):
        delivery = self.delivery()
        self.assertEqual(delivery['identity']['stack_sources'], {})
        self.assertEqual(standalone.validate_delivery(delivery), delivery)
        with patch.object(standalone, 'fingerprint', return_value='a' * 64):
            with self.assertRaisesRegex(ValueError, 'recipe'):
                standalone.validate_delivery(delivery)
        changed = copy.deepcopy(delivery)
        changed['lammps'] = {'source_sha': 'a' * 40}
        with self.assertRaisesRegex(ValueError, 'unexpected'):
            standalone.validate_delivery(changed)

    def test_every_target_has_project_billing_correct_qos_and_final_sif_science(self):
        for target in standalone.GPU_TARGETS:
            with self.subTest(target=target):
                script = standalone.render(self.delivery(target), 'independent-test')
                subprocess.run(['bash', '-n'], input=script, text=True, check=True)
                self.assertIn('#SBATCH --account=proj-sai-hpc-software\n', script)
                self.assertIn('#SBATCH --chdir=/org/org-open-sai/proj-sai-hpc-software/', script)
                self.assertIn('#SBATCH --qos=' + ('flood-1o2gpu' if target == '16v100-avx2' else 'rush-1o2gpu'), script)
                self.assertIn('#SBATCH --gpus-per-node=2', script)
                self.assertIn('BUILD_JOBS=12', script)
                self.assertIn('cp2k_image_driver.py', script)
                self.assertNotIn('afterok:', script)
                self.assertNotIn('/input/lammps', script)
                self.assertNotIn('TMPDIR=/tmp', script)
                self.assertNotIn('/home/stardust', script)
                final = [line for line in script.splitlines() if 'candidate.sif' in line and ' science ' in line]
                self.assertEqual(len(final), 1)
                self.assertNotIn('--fakeroot', final[0])
                self.assertNotIn('--overlay', final[0])
                self.assertIn('--nv', final[0])

    def test_missing_backend_or_gpu_evidence_cannot_be_scientific_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(FileNotFoundError):
                verify_science_files(temporary)

    def test_native_library_soname_symlinks_hash_the_actual_library(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary)
            site = prefix / 'lib/python3.13/site-packages/deepmd/lib'
            site.mkdir(parents=True)
            library = prefix / 'lib/libdeepmd_c.so.3'
            library.write_bytes(b'native-library')
            (prefix / 'lib/libdeepmd_c.so').symlink_to(library.name)
            for name in ('tf', 'pt', 'jax'):
                (site / ('libdeepmd_backend_' + name + '.so')).write_bytes(name.encode())
            with patch('deepmd_standalone_check.libraries', return_value='resolved dependencies'), \
                    patch('deepmd_standalone_check.importlib.metadata.version', return_value='3.2.0'):
                observed = inventory(prefix)
            self.assertEqual(observed['libraries'][str(prefix / 'lib/libdeepmd_c.so')]['resolved'], str(library))

    def test_fingerprint_covers_every_executed_source_and_detects_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            control = Path(temporary)
            for name in standalone.FILES:
                (control / name).write_bytes((ROOT / 'controller' / name).read_bytes())
            before = standalone.fingerprint(control)
            for name in ('md_science.py', 'md_environment.sh', 'deepmd_standalone_entry.sh'):
                path = control / name
                original = path.read_bytes()
                path.write_bytes(original + b'\n# changed\n')
                self.assertNotEqual(before, standalone.fingerprint(control))
                path.write_bytes(original)


if __name__ == '__main__':
    unittest.main()
