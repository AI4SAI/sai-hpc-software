"""Export recovery must preserve the failed build and isolate all runtime scratch."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'controller'))
spec = importlib.util.spec_from_file_location('deepmd_export_recovery',
    ROOT / 'docs/evidence/deepmd-project-20261023/recover-8v100v0.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class RecoveryTests(unittest.TestCase):
    def test_export_and_science_keep_original_recipe_and_project_backed_tmp(self):
        run = recovery.ROOT / 'runs/recovery-test'
        record = dict(controller=str(ROOT / 'controller'),
            acceptance_controller=str(recovery.CHECKER),
            delivery=dict(schema=1, identity=dict(install_prefix='/opt/apps/deepmd-kit/candidate/8V100V0')))
        with patch.object(recovery, 'context', return_value=record):
            script = recovery.render(run)
        subprocess.run(['bash', '-n'], input=script, text=True, check=True)
        self.assertIn('#SBATCH --account=proj-sai-hpc-software\n', script)
        self.assertIn('#SBATCH --chdir=' + str(run) + '\n', script)
        self.assertIn(str(ROOT / 'controller') + ':/control:ro', script)
        self.assertIn(str(recovery.CHECKER) + ':/acceptance:ro', script)
        self.assertNotIn('/control/deepmd_standalone_entry.sh build ', script)
        commands = [line for line in script.splitlines() if '--containall' in line]
        self.assertEqual(len(commands), 4)
        for command in commands:
            self.assertIn(str(run / 'runtime') + ':/tmp:rw', command)
            self.assertNotIn('--bind /tmp:', command)

    def fixture(self, root):
        (root / 'results').mkdir()
        (root / 'runtime').mkdir()
        (root / 'job.id').write_text('456\n')
        (root / 'job.sbatch').write_text('export-only script\n')
        (root / 'candidate.sif').write_bytes(b'candidate')
        (root / 'candidate.sif').chmod(0o444)
        (root / 'request.json').write_text('frozen request\n')
        recovery.write(root / 'submission.json', dict(script_sha256=recovery.digest(root / 'job.sbatch'),
                                                     recovery_tools_commit='b' * 40))
        recovery.write(root / 'runtime/science.json', dict(passed=True))
        (root / 'results/slurm-456.log').write_text('DEEPMD_EXPORT_RECOVERY_BUILD_VERIFIED\n'
            'DEEPMD_EXPORT_RECOVERY_SCIENCE_VERIFIED_NOT_PUBLISHED\n')
        return dict(operation='recover-deepmd-export-and-science', source_job='123',
            source_overlay_sha256='a' * 64, delivery={'identity': {'source_sha': recovery.SOURCE_SHA}},
            acceptance_controller=str(recovery.CHECKER), acceptance_commit=recovery.CHECKER_COMMIT)

    def test_completed_recovery_never_relabels_original_job_or_claims_native_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = self.fixture(root)
            row = ['456', 'COMPLETED', '0:0', '8V100V0', recovery.ACCOUNT, str(root)]
            with patch.object(recovery, 'context', return_value=record), \
                    patch.object(recovery, 'accounting', return_value=row), \
                    patch.object(recovery, 'science_files', return_value={'passed': True}):
                result = recovery.status(root)
            self.assertEqual(result['source_job_state'], 'FAILED')
            self.assertTrue(result['build_verified'])
            self.assertTrue(result['scientific_verified'])
            self.assertFalse(result['native_runtime_verified'])
            self.assertFalse(result['published'])

    def test_wrong_account_or_changed_raw_science_is_rejected(self):
        for changed in ('account', 'raw'):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                record = self.fixture(root)
                row = ['456', 'COMPLETED', '0:0', '8V100V0', recovery.ACCOUNT, str(root)]
                if changed == 'account':
                    row[4] = 'private-stardust'
                with patch.object(recovery, 'context', return_value=record), \
                        patch.object(recovery, 'accounting', return_value=row), \
                        patch.object(recovery, 'science_files', return_value={'passed': False}), \
                        self.assertRaises(ValueError):
                    recovery.status(root)


if __name__ == '__main__':
    unittest.main()
