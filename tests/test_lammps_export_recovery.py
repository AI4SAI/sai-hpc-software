"""A recovered export must not relabel the original failed allocation."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('lammps_export_recovery',
    ROOT / 'docs/evidence/lammps-project-20261023/recover.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class RecoveryTests(unittest.TestCase):
    def fixture(self, root):
        (root / 'results').mkdir()
        (root / 'job.id').write_text('456\n')
        (root / 'job.sbatch').write_text('export-only script\n')
        (root / 'candidate.sif').write_bytes(b'candidate')
        (root / 'request.json').write_text('frozen request\n')
        recovery.write(root / 'submission.json', dict(script_sha256=recovery.digest(root / 'job.sbatch')))
        (root / 'results/slurm-456.log').write_text(
            'LAMMPS_STANDALONE_SIF_INVENTORY_VERIFIED\n'
            'LAMMPS_STANDALONE_EXPORT_RECOVERY_VERIFIED_NOT_PUBLISHED\n')
        return dict(operation='recover-standalone-export', source_job='123',
                    source_overlay_sha256='a' * 64, delivery={'identity': {'source_sha': recovery.SOURCE_SHA}})

    def test_success_is_a_separate_project_export_and_not_scientific_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = self.fixture(root)
            row = ['456', 'COMPLETED', '0:0', '8V100V0', recovery.ACCOUNT, str(root)]
            with patch.object(recovery, 'context', return_value=record), \
                    patch.object(recovery, 'accounting', return_value=row):
                result = recovery.status(root)
            self.assertEqual(result['job'], '456')
            self.assertEqual(result['source_job'], '123')
            self.assertEqual(result['source_job_state'], 'FAILED')
            self.assertTrue(result['build_verified'])
            for field in ('scientific_verified', 'native_runtime_verified', 'published'):
                self.assertFalse(result[field])

    def test_private_account_wrong_workdir_and_missing_inventory_are_rejected(self):
        for change in ('account', 'workdir', 'marker', 'script'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                record = self.fixture(root)
                row = ['456', 'COMPLETED', '0:0', '8V100V0', recovery.ACCOUNT, str(root)]
                if change == 'account':
                    row[4] = 'personal-account'
                elif change == 'workdir':
                    row[5] = '/tmp'
                elif change == 'marker':
                    (root / 'results/slurm-456.log').write_text(
                        'LAMMPS_STANDALONE_EXPORT_RECOVERY_VERIFIED_NOT_PUBLISHED\n')
                else:
                    (root / 'job.sbatch').write_text('changed\n')
                with patch.object(recovery, 'context', return_value=record), \
                        patch.object(recovery, 'accounting', return_value=row), self.assertRaises(ValueError):
                    recovery.status(root)


if __name__ == '__main__':
    unittest.main()
