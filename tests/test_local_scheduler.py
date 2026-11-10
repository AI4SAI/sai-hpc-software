"""Scheduling boundaries and retries must not multiply real cluster work."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('local_scheduler',
    REPO / 'deployment/self-hosted-relay/dispatch_daily.py')
scheduler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scheduler)


class LocalSchedulerTests(unittest.TestCase):
    now = datetime(2026, 11, 10, 19, 0, tzinfo=timezone.utc)  # Nov 11 03:00 in China

    def plan(self, state='succeeded', **values):
        return dict(state=state, selection=dict(scheduler.SELECTION), retry_releases=False,
                    created=self.now.isoformat(), **values)

    def test_daily_boundary_is_three_am_shanghai_not_utc_midnight(self):
        self.assertEqual(scheduler.due_date(self.now - timedelta(seconds=1)), '2026-11-10')
        self.assertEqual(scheduler.due_date(self.now), '2026-11-11')
        self.assertEqual(scheduler.due_date(self.now + timedelta(hours=20)), '2026-11-11')

    def test_busy_queued_or_waiting_work_prevents_another_dispatch(self):
        for state in ('in_progress', 'queued', 'waiting', 'requested', 'pending'):
            with self.subTest(state=state):
                self.assertEqual(scheduler.decide(self.now, [], [{'status': state}], None), ('busy', ''))

    def test_active_plan_recovers_before_a_new_day_is_started(self):
        plan = self.plan('active', schedule_date='2026-11-09')
        self.assertEqual(scheduler.decide(self.now, [plan], [], None), ('resume', ''))

    def test_manual_full_build_counts_but_partial_or_smoke_does_not(self):
        self.assertEqual(scheduler.decide(self.now, [self.plan()], [], None),
                         ('already-requested', '2026-11-11'))
        partial = self.plan(); partial['selection']['software'] = 'gpumd'
        smoke = dict(status='completed', display_title='SAI smoke / 42')
        self.assertEqual(scheduler.decide(self.now, [partial], [smoke], None), ('start', '2026-11-11'))

    def test_failed_daily_attempt_is_not_automatically_rebuilt_repeatedly(self):
        for plans, runs in (([self.plan('failed')], []),
                            ([], [dict(status='completed', conclusion='failure',
                                       display_title='SAI daily 2026-11-11 / start')])):
            self.assertEqual(scheduler.decide(self.now, plans, runs, None),
                             ('already-requested', '2026-11-11'))

    def test_lost_post_reply_is_retained_and_visibility_is_reconciled(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            api = Mock()
            api.runs.return_value = []
            api.dispatch.side_effect = TimeoutError('reply lost')
            with self.assertRaises(TimeoutError):
                scheduler.tick(root / 'scheduler', root / 'plans', api, self.now)
            receipt = json.loads((root / 'scheduler/receipt.json').read_text())
            self.assertFalse(receipt['accepted'])
            self.assertEqual(scheduler.tick(root / 'scheduler', root / 'plans', api,
                                           self.now + timedelta(minutes=5)), 'awaiting-dispatch-visibility')
            api.dispatch.assert_called_once_with('start', '2026-11-11')
            api.runs.return_value = [dict(status='completed', display_title='SAI daily 2026-11-11 / start')]
            self.assertEqual(scheduler.tick(root / 'scheduler', root / 'plans', api,
                                           self.now + timedelta(minutes=20)), 'already-requested')
            api.dispatch.assert_called_once()

    def test_missing_dispatch_is_retried_after_grace_with_same_daily_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            api = Mock(); api.runs.return_value = []
            scheduler.tick(root / 'scheduler', root / 'plans', api, self.now)
            scheduler.tick(root / 'scheduler', root / 'plans', api, self.now + timedelta(minutes=20))
            self.assertEqual(api.dispatch.call_count, 2)
            self.assertEqual(api.dispatch.call_args_list[0], api.dispatch.call_args_list[1])

    def test_token_cannot_be_world_readable_or_redirected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'token'; path.write_text('test-only-token\n')
            path.chmod(0o644)
            with self.assertRaises(ValueError): scheduler.GitHub(path)
            path.chmod(0o600)
            scheduler.GitHub(path)
            link = path.with_name('redirect'); link.symlink_to(path)
            with self.assertRaises(ValueError): scheduler.GitHub(link)


if __name__ == '__main__':
    unittest.main()
