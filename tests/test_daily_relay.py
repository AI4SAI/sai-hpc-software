"""Exercise interruption windows and scheduling, without submitting real jobs."""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'controller'))
import daily_relay as relay
import relay_checkpoint as checkpoint
from release_contract import claim_build, make_identity, resolve_tracks
from ci import install_controller


class ControllerUploadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.destination = self.root / 'controller'
        self.destination.mkdir()
        self.contents = {'gpumd': b'#!/bin/sh\nexec gpumd "$@"\n', 'recipe.py': b'print("recipe")\n'}
        self.manifest = {name: hashlib.sha256(data).hexdigest() for name, data in self.contents.items()}

    def stage(self, suffix):
        staging = self.root / ('controller.upload-' + suffix)
        staging.mkdir()
        for name, data in self.contents.items():
            (staging / name).write_bytes(data)
        return staging

    def test_retry_reuses_readonly_launcher_without_rewriting_its_inode(self):
        install_controller(self.stage('first'), self.destination, self.manifest, ['gpumd'])
        launcher = self.destination / 'gpumd'
        original = launcher.stat()
        self.assertEqual(original.st_mode & 0o777, 0o555)
        install_controller(self.stage('retry'), self.destination, self.manifest, ['gpumd'])
        self.assertEqual(launcher.stat().st_ino, original.st_ino)
        self.assertEqual(launcher.stat().st_mtime_ns, original.st_mtime_ns)

    def test_truncated_upload_does_not_publish_any_controller_file(self):
        staging = self.stage('partial')
        (staging / 'recipe.py').write_bytes(b'partial')
        with self.assertRaisesRegex(ValueError, 'incomplete controller upload'):
            install_controller(staging, self.destination, self.manifest, ['gpumd'])
        self.assertEqual(list(self.destination.iterdir()), [])
        install_controller(self.stage('retry'), self.destination, self.manifest, ['gpumd'])

    def test_interrupted_commit_can_resume_without_overwriting_prior_files(self):
        staging = self.stage('partial')
        link = os.link
        calls = []
        def interrupted(source, target):
            calls.append(target)
            if len(calls) == 2:
                raise OSError('interrupted')
            link(source, target)
        with patch('os.link', side_effect=interrupted), self.assertRaises(OSError):
            install_controller(staging, self.destination, self.manifest, ['gpumd'])
        install_controller(self.stage('retry'), self.destination, self.manifest, ['gpumd'])
        for name, content in self.contents.items():
            self.assertEqual((self.destination / name).read_bytes(), content)

    def test_different_existing_controller_fails_before_any_commit(self):
        (self.destination / 'recipe.py').write_bytes(b'other recipe')
        with self.assertRaisesRegex(ValueError, 'differs from the frozen source'):
            install_controller(self.stage('mismatch'), self.destination, self.manifest, ['gpumd'])
        self.assertFalse((self.destination / 'gpumd').exists())
        self.assertEqual((self.destination / 'recipe.py').read_bytes(), b'other recipe')


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.task = self.root / 'run'
        self.task.mkdir()
        env = patch.dict(os.environ, SAI_RELAY_TASK=str(self.root / 'checkpoint.json'))
        env.start()
        self.addCleanup(env.stop)
        transport = patch.object(checkpoint, 'remote', side_effect=lambda ssh, fn, *args: fn(*args))
        transport.start()
        self.addCleanup(transport.stop)
        self.submissions = 0

    def submit(self):
        self.submissions += 1
        script = b'#!/bin/sh\ntrue\n'
        (self.task / 'job.sbatch').write_bytes(script)
        (self.task / 'job.id').write_text('123\n')
        (self.task / 'request.json').write_text(json.dumps(dict(controller='frozen', job_id='123',
            job_script_sha256=hashlib.sha256(script).hexdigest())))

    def test_pending_restart_recovers_same_job_and_never_resubmits(self):
        first = checkpoint.Checkpoint()
        first.submitted(None, str(self.task), dict(controller='frozen'), self.submit)
        with patch('subprocess.check_output', return_value=f'123|RUNNING|None|{self.task}\n'):
            with self.assertRaises(checkpoint.Pending):
                first.wait(None, str(self.task))
        second = checkpoint.Checkpoint()
        second.submitted(None, str(self.task), dict(controller='frozen'), self.submit)
        with patch('subprocess.check_output', side_effect=['', f'123|COMPLETED|0:0|{self.task}|\n']):
            second.wait(None, str(self.task))
        self.assertEqual(self.submissions, 1)
        self.assertEqual(second.data['pending'], [])

    def test_lost_submission_reply_recovers_complete_receipt(self):
        def lost():
            self.submit()
            raise subprocess.CalledProcessError(255, ['ssh'])
        with self.assertRaises(subprocess.CalledProcessError):
            checkpoint.Checkpoint().submitted(None, str(self.task), dict(controller='frozen'), lost)
        recovered = checkpoint.Checkpoint().submitted(None, str(self.task), dict(controller='frozen'), self.submit)
        self.assertEqual(recovered['job_id'], '123')
        self.assertEqual(self.submissions, 1)

    def test_incomplete_submission_and_changed_script_fail_without_sbatch(self):
        with self.assertRaises(subprocess.CalledProcessError):
            checkpoint.Checkpoint().submitted(None, str(self.task), {},
                Mock(side_effect=subprocess.CalledProcessError(255, ['ssh'])))
        with self.assertRaisesRegex(ValueError, 'incomplete submission'):
            checkpoint.Checkpoint().submitted(None, str(self.task), {}, self.submit)
        self.assertEqual(self.submissions, 0)
        self.submit()
        (self.task / 'job.sbatch').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'script changed'):
            checkpoint.Checkpoint().submitted(None, str(self.task), {}, self.submit)
        self.assertEqual(self.submissions, 1)

    def test_freeze_survives_midnight_and_rejects_another_controller(self):
        first = checkpoint.Checkpoint()
        first.freeze('run_id', 'original-date-run')
        first.bind(dict(source='a', controller='b'))
        second = checkpoint.Checkpoint()
        self.assertEqual(second.freeze('run_id', 'new-date-run'), 'original-date-run')
        with self.assertRaisesRegex(ValueError, 'another source'):
            second.bind(dict(source='a', controller='c'))

    def test_accounting_provenance_and_held_release(self):
        record = dict(job='123', directory=str(self.task))
        with patch('subprocess.check_output', side_effect=['', '123|COMPLETED|0:0|/somewhere-else|\n']):
            with self.assertRaisesRegex(ValueError, 'work directory'):
                checkpoint.inspect_jobs([record])
        with patch('subprocess.check_output', return_value=f'123|PENDING|JobHeldUser|{self.task}\n'), \
                patch('subprocess.run') as release:
            self.assertEqual(checkpoint.inspect_jobs([record], True), ['123'])
            release.assert_called_once_with(['scontrol', 'release', '123'], check=True)


class RelayTests(unittest.TestCase):
    @staticmethod
    def sources(software, tracks):
        return resolve_tracks(software, tracks, resolver=lambda repo, ref:
            dict(sha='a' * 40, ref=ref if ref not in ('latest-release', 'latest-prerelease') else 'v1', version='v1'))

    def test_every_partition_can_submit_before_waiting(self):
        generation = relay.make_plan('gpumd', 'development', 'all', self.sources)
        self.assertEqual(len(generation['tasks']), 4)
        first = list(relay.ready_tasks(generation, {}, set()))
        self.assertEqual(len(first), 3)
        first[0]['state'] = 'waiting'
        saved = {first[0]['id']: dict(pending=[dict(job='123')])}
        ready = list(relay.ready_tasks(generation, saved, {'123'}))
        self.assertEqual([task['id'] for task in ready], [task['id'] for task in first[1:]])
        first[0]['state'] = 'failed'
        for task in first[1:]:
            task['state'] = 'succeeded'
        self.assertEqual(list(relay.ready_tasks(generation, {}, set())), [])
        self.assertEqual(generation['tasks'][-1]['state'], 'blocked')

    def test_beta_depends_on_all_development_targets_not_release_skips(self):
        generation = relay.make_plan('gpumd', 'development,release', 'all', self.sources)
        for task in generation['tasks'][:-1]:
            task['state'] = 'succeeded' if task['source']['track'] == 'development' else 'skipped'
        self.assertEqual([task['kind'] for task in relay.ready_tasks(generation, {}, set())], ['beta'])
        partial = relay.make_plan('gpumd', 'development', '16v100-avx2', self.sources)
        self.assertEqual([task['kind'] for task in partial['tasks']], ['build'])

    def test_resume_environment_keeps_origin_and_uses_current_ephemeral_key(self):
        generation = dict(workflow='100', attempt='1', controller='a' * 40, user='cluster', retry_releases=False)
        task = relay.make_plan('qe', 'development', 'dsprhbm', self.sources)['tasks'][0]
        with patch.dict(os.environ, GITHUB_RUN_ID='999', GITHUB_RUN_ATTEMPT='5', GITHUB_SHA='b' * 40,
                        GH_TOKEN='current-token', CI_PHASE='acceptance', RESUME_RUN='wrong'):
            script, env = relay.task_environment(generation, task, Path('/state/task'), Path('/ephemeral/key'))
        self.assertEqual(script, 'ci.py')
        self.assertEqual((env['GITHUB_RUN_ID'], env['GITHUB_RUN_ATTEMPT'], env['GITHUB_SHA']), ('100', '1', 'a' * 40))
        self.assertEqual(env['SAI_RELAY_SSH_KEY'], '/ephemeral/key')
        self.assertEqual(env['GH_TOKEN'], 'current-token')
        self.assertNotIn('RESUME_RUN', env)
        self.assertNotIn('CI_PHASE', env)

    def test_failed_child_is_failed_and_pending_is_not_acceptance(self):
        for code, expected in ((0, 'succeeded'), (75, 'waiting'), (76, 'waiting'), (1, 'failed')):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary).resolve() / 'plan.json'
                generation = relay.make_plan('gpumd', 'development', '16v100-avx2', self.sources)
                generation.update(workflow='1', attempt='1', controller='a' * 40, user='cluster', retry_releases=False)
                task = generation['tasks'][0]
                relay.advance(path, task, generation, Path('/ephemeral/key'),
                              run=Mock(return_value=subprocess.CompletedProcess([], code)))
                self.assertEqual(task['state'], expected)
                self.assertEqual(relay.read(path)['tasks'][0]['state'], expected)

    def test_remote_python_snippets_compile_after_branching(self):
        for name in ('deepmd_ci.py', 'beta_ci.py'):
            tree = ast.parse((REPO / 'controller' / name).read_text())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                        and any(isinstance(t, ast.Name) and t.id in ('prepare', 'collect', 'unpack', 'collect_metadata')
                                for t in node.targets)):
                    compile(node.value.value, name, 'exec')


class ClaimResumeTests(unittest.TestCase):
    def test_only_exact_frozen_claim_can_resume(self):
        identity = make_identity('cp2k', 'release', 'v2026.2', 'a' * 40, 'v2026.2', 'b' * 64, 'dsprhbm')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            original = claim_build(root, [identity], 'run-one')
            self.assertEqual(claim_build(root, [identity], 'run-one', resume=True), original)
            skipped = claim_build(root, [identity], 'run-two')
            self.assertFalse(skipped['build'])
            self.assertEqual(claim_build(root, [identity], 'run-two', resume=True), skipped)
            for kwargs in (dict(retry=True), dict(identities=[make_identity('cp2k', 'release', 'v2026.2', 'c' * 40, 'v2026.2', 'b' * 64, 'dsprhbm')])):
                arguments = dict(identities=[identity], run_id='run-one', resume=True)
                arguments.update(kwargs)
                with self.assertRaisesRegex(ValueError, 'differs'):
                    claim_build(root, **arguments)


if __name__ == '__main__':
    unittest.main()
