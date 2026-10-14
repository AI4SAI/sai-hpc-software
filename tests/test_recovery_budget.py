from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import recovery_budget as budget
import submit_abacus_recovery as recovery


class RecoveryBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ledger = self.root / 'attempts/abacus'
        self.ledger.mkdir(parents=True)
        (self.ledger / 'lock').touch()
        budget.write_new(self.root / 'authorization.json', dict(
            authorization_id=budget.BASE_ID, additional_attempt_limit_per_software=6))
        for number in range(1, 7):
            budget.write_new(self.ledger / f'{number:02d}.json', dict(
                authorization_id=budget.BASE_ID, number=number, limit=6,
                software='abacus', run_id=f'old-{number}', status='released'))

    def test_explicit_extension_preserves_history_and_other_software(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*.json')}
        self.assertEqual(budget.status(self.root)['remaining'], 0)
        budget.register_extension(self.root)
        budget.register_extension(self.root)
        self.assertEqual(budget.status(self.root)['remaining'], 6)
        self.assertEqual(budget.status(self.root)['limit'], 12)
        for path, contents in before.items():
            self.assertEqual(path.read_bytes(), contents)
        self.assertEqual([p.name for p in (self.root / 'attempts').iterdir()], ['abacus'])

    def test_no_extension_cannot_claim_seventh(self):
        with self.assertRaisesRegex(ValueError, 'exhausted'):
            budget.claim(self.root, 'seventh', 'build', 'fixed a reproduced error')
        self.assertEqual(len(list(self.ledger.glob('*.json'))), 6)

    def test_explicit_unlimited_grant_preserves_all_prior_grants_and_receipts(self):
        budget.register_extension(self.root)
        before = {p: p.read_bytes() for p in self.root.rglob('*.json')}
        budget.register_unlimited(self.root)
        budget.register_unlimited(self.root)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)
        for number in range(7, 15):
            path, record = budget.claim(self.root, f'new-{number}', 'build', 'diagnosed and tested correction')
            budget.receipt(path, record, str(number), 'submitted-held', self.root / record['run_id'])
            budget.receipt(path, record, str(number), 'released', self.root / record['run_id'])
        self.assertIsNone(budget.status(self.root)['remaining'])
        self.assertIsNone(budget.status(self.root)['limit'])
        self.assertEqual(budget.status(self.root)['used'], 14)
        with self.assertRaisesRegex(ValueError, 'diagnostic reason'):
            budget.claim(self.root, 'blind', 'build', ' ')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            budget.claim(self.root, 'new-14', 'build', 'diagnosed')
        budget.claim(self.root, 'pending', 'build', 'diagnosed')
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            budget.claim(self.root, 'another', 'build', 'diagnosed')

    def test_unlimited_applies_to_other_software_without_rewriting_their_ledgers(self):
        for software in ('cp2k', 'lammps'):
            directory = self.root / 'attempts' / software
            directory.mkdir()
            (directory / 'lock').touch()
            self.assertEqual(budget.status(self.root, software)['limit'], 6)
        budget.register_unlimited(self.root)
        for software in ('cp2k', 'lammps'):
            path, record = budget.claim(self.root, software + '-new', 'build', 'diagnosed', software)
            budget.receipt(path, record, '42', 'submitted-held', self.root / (software + '-new'))
            budget.receipt(path, record, '42', 'released', self.root / (software + '-new'))
            self.assertIsNone(budget.status(self.root, software)['limit'])

    def test_changed_unlimited_policy_is_rejected(self):
        budget.register_unlimited(self.root)
        path = self.root / budget.UNLIMITED_FILE
        path.write_text(json.dumps(dict(budget.UNLIMITED, requirements=[])))
        with self.assertRaisesRegex(ValueError, 'unexpected unlimited'):
            budget.status(self.root)

    def test_build_probe_and_acceptance_share_limit_and_preserve_job_id(self):
        budget.register_extension(self.root)
        for number, kind in enumerate(('build', 'probe', 'acceptance') * 2, 7):
            path, record = budget.claim(self.root, f'new-{number}', kind, 'reviewed fix or validation')
            self.assertEqual(record['number'], number)
            self.assertEqual(record['limit'], 12)
            budget.receipt(path, record, str(number), 'submitted-held', self.root / record['run_id'])
            budget.receipt(path, record, str(number), 'released', self.root / record['run_id'])
        self.assertEqual(budget.status(self.root)['remaining'], 0)
        with self.assertRaisesRegex(ValueError, 'exhausted'):
            budget.claim(self.root, 'thirteenth', 'build', 'not authorized')

    def test_uncertain_submission_is_not_silently_retried(self):
        budget.register_extension(self.root)
        path, record = budget.claim(self.root, 'uncertain', 'build', 'diagnosed')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            budget.claim(self.root, 'uncertain', 'build', 'retry')
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            budget.claim(self.root, 'another', 'build', 'retry')
        with self.assertRaisesRegex(ValueError, 'transition'):
            budget.receipt(path, record, '100', 'released', self.root / 'run')
        budget.receipt(path, record, '100', 'submitted-held', self.root / 'run')
        with self.assertRaisesRegex(ValueError, 'replace'):
            budget.receipt(path, record, '101', 'released', self.root / 'run')

    def test_parallel_claims_cannot_duplicate_reserved_slot(self):
        budget.register_extension(self.root)
        def reserve(index):
            try:
                return budget.claim(self.root, f'parallel-{index}', 'build', 'diagnosed')[1]['number']
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(reserve, range(6)))
        self.assertEqual(sorted(n for n in results if n is not None), [7])

    def test_changed_extension_symlink_or_noncontiguous_history_fails_closed(self):
        budget.register_extension(self.root)
        extension = self.root / budget.EXTENSION_FILE
        changed = dict(budget.EXTENSION, total_attempt_limit=18)
        extension.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, 'extension'):
            budget.status(self.root)
        extension.write_text(json.dumps(budget.EXTENSION))
        last = self.ledger / '06.json'
        saved = last.read_bytes()
        last.unlink()
        last.symlink_to(self.ledger / '05.json')
        with self.assertRaisesRegex(ValueError, 'unaliased'):
            budget.status(self.root)
        last.unlink()
        last.write_bytes(saved)
        last.rename(self.ledger / '07.json')
        with self.assertRaisesRegex(ValueError, 'noncontiguous'):
            budget.status(self.root)

    def test_retry_rejects_active_successful_and_unknown_jobs(self):
        previous = self.root / 'runs/previous'
        previous.mkdir(parents=True)
        budget.write_new(previous / 'request.json', dict(software='abacus', version='v3.11.0-beta9',
            sha='149723287702677dc159c5e96fb98cbc3482f445', target='16v100-avx2'))
        (previous / 'job.id').write_text('100\n')
        for outputs, message in ((['100 RUNNING'], 'active'),
                                 (['', '100|COMPLETED|0:0|\n'], 'terminal'),
                                 (['', ''], 'terminal'),
                                 (['', '100|RUNNING|0:0|\n'], 'terminal')):
            with self.subTest(outputs=outputs), patch.object(recovery.controller, 'ROOT', self.root), \
                    patch.object(recovery.subprocess, 'check_output', side_effect=outputs), \
                    self.assertRaisesRegex(ValueError, message):
                recovery.require_failed_build('previous')
        with patch.object(recovery.controller, 'ROOT', self.root), \
                patch.object(recovery.subprocess, 'check_output', side_effect=['', '100|FAILED|1:0|\n']):
            self.assertEqual(recovery.require_failed_build('previous')['state'], 'FAILED')

    def exercise_submission(self, *, scheduler_reply='123;cluster', preflight_error=False):
        budget.register_extension(self.root)
        run = self.root / 'runs/seventh'
        events = []

        def command(argv, **kwargs):
            events.append(argv[:2])
            if argv[:2] == ['sbatch', '--test-only']:
                self.assertEqual(budget.status(self.root)['used'], 6)
                if preflight_error:
                    raise subprocess.CalledProcessError(1, argv)
            if argv[:2] == ['scontrol', 'release']:
                recorded = budget.read_json(self.ledger / '07.json')
                self.assertEqual(recorded['status'], 'submitted-held')
                self.assertEqual(recorded['job_id'], '123')
                self.assertEqual((run / 'job.id').read_text().strip(), '123')

        def held(argv, **kwargs):
            self.assertEqual(argv[:3], ['sbatch', '--hold', '--parsable'])
            self.assertIn(['sbatch', '--test-only'], events)
            self.assertEqual(budget.read_json(self.ledger / '07.json')['status'], 'claimed')
            self.assertFalse(budget.read_json(run / 'request.json')['published'])
            events.append(argv[:2])
            return scheduler_reply

        with ExitStack() as stack:
            stack.enter_context(patch.object(recovery.controller, 'ROOT', self.root))
            stack.enter_context(patch.object(recovery, 'require_failed_build', return_value={
                'run_id': 'old-6', 'job_id': '100', 'state': 'FAILED', 'exit_code': '1:0'}))
            stack.enter_context(patch.object(recovery, 'status', side_effect=lambda _: budget.status(self.root)))
            stack.enter_context(patch.object(recovery, 'claim', side_effect=lambda _, *args: budget.claim(self.root, *args)))
            stack.enter_context(patch.object(recovery, 'load_lock', return_value={'full_features': True}))
            stack.enter_context(patch.object(recovery, 'cache_archives'))
            stack.enter_context(patch.object(recovery.subprocess, 'run', side_effect=command))
            stack.enter_context(patch.object(recovery.subprocess, 'check_output', side_effect=held))
            return recovery.submit('seventh', 'old-6', 'reproduced MPI prefix shadowing and tested fix')

    def test_submission_records_held_job_before_release(self):
        result = self.exercise_submission()
        self.assertEqual(result['attempt'], 7)
        self.assertEqual(result['limit'], 12)
        self.assertEqual(result['job_id'], '123')
        record = budget.read_json(self.ledger / '07.json')
        self.assertEqual(record['status'], 'released')
        request = budget.read_json(self.root / 'runs/seventh/request.json')
        self.assertEqual((request['jobs'], request['minutes'], request['overlay_mb']), (4, 240, 32768))
        self.assertEqual(request['retry_authorization']['number'], 7)

    def test_invalid_submission_receipt_blocks_followup_claim(self):
        with self.assertRaisesRegex(ValueError, 'scheduler receipt'):
            self.exercise_submission(scheduler_reply='unknown')
        self.assertEqual(budget.read_json(self.ledger / '07.json')['status'], 'claimed')
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            budget.claim(self.root, 'eighth', 'build', 'must reconcile first')

    def test_resource_preflight_failure_does_not_spend_attempt(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.exercise_submission(preflight_error=True)
        self.assertEqual(budget.status(self.root)['used'], 6)


if __name__ == '__main__':
    unittest.main()
