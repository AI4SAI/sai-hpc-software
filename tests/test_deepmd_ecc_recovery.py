"""Recover only a recorded hardware failure while retaining the original proof."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import deepmd_acceptance as acceptance


class ECCRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        original = self.root / 'original'
        (original / 'runtime/training/se_e2_a').mkdir(parents=True)
        (original / 'results').mkdir()
        self.log = original / 'runtime/training/se_e2_a/training.log'
        self.log.write_text('RuntimeError: CUDA error: uncorrectable ECC error encountered')
        self.source = dict(source_run='original', build_accounting='123|FAILED|1:0|00:00:05|8v100v0n09|proj-sai-hpc-software|' + str(original))
        for p in (patch.object(acceptance, 'task', side_effect=lambda run: self.root / run),
                  patch.object(acceptance, 'origin', return_value=self.source)):
            p.start(); self.addCleanup(p.stop)

    def test_science_error_is_not_retried(self):
        self.log.write_text('energy outside tolerance')
        with patch.object(acceptance, 'submit') as submit, self.assertRaisesRegex(ValueError, 'no recoverable'):
            acceptance.recover('original')
        submit.assert_not_called()

    def test_ecc_recovery_has_one_durable_allocation_and_keeps_original_log(self):
        old = self.log.read_bytes()
        def submitted(run, source, exclude):
            directory = self.root / run
            directory.mkdir()
            (directory / 'request.json').write_text(json.dumps(dict(source=self.source, controller=str(acceptance.CONTROL), exclude=exclude, job_id='456')))
        with patch.object(acceptance, 'submit', side_effect=submitted) as submit, \
                patch.object(acceptance.subprocess, 'check_output', return_value=''), redirect_stdout(io.StringIO()):
            acceptance.recover('original')
            acceptance.recover('original')
        submit.assert_called_once_with('original-ecc-retry1', 'original', '8v100v0n09')
        self.assertEqual(self.log.read_bytes(), old)
        pointer = json.loads((self.root / 'original/results/acceptance-recovery.json').read_text())
        self.assertEqual(pointer['source_run'], 'original')
        pointer['source_run'] = 'other'
        path = self.root / 'original/results/acceptance-recovery.json'
        path.chmod(0o644); path.write_text(json.dumps(pointer))
        with self.assertRaisesRegex(ValueError, 'pointer differs'):
            acceptance.recover('original')

    def test_interrupted_held_recovery_is_reattached_without_second_submission(self):
        directory = self.root / 'original-ecc-retry1'
        directory.mkdir()
        (directory / 'request.json').write_text(json.dumps(dict(source=self.source, controller=str(acceptance.CONTROL), exclude='8v100v0n09', job_id='456')))
        with patch.object(acceptance, 'submit') as submit, \
                patch.object(acceptance.subprocess, 'check_output', return_value='PENDING|JobHeldUser\n'), \
                patch.object(acceptance.subprocess, 'run') as release, redirect_stdout(io.StringIO()):
            acceptance.recover('original')
        submit.assert_not_called()
        release.assert_called_once_with(['scontrol', 'release', '456'], check=True)


if __name__ == '__main__':
    unittest.main()
