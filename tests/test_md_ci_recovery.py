"""Persisted orchestration recovery; fixture bytes are not scientific evidence."""
from contextlib import ExitStack
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import md_ci
import md_controller as build
import md_acceptance_controller as science
import md_performance_controller as performance
import md_tracking
from source_cache import checksum
from test_md_controller import delivery_request


class MDContinuationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.project = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.root = self.project / 'experimental/deepmd-lammps'
        self.control = self.root / 'controller/snapshot/build'
        self.control.mkdir(parents=True)
        self.run_id = 'build'
        self.delivery = delivery_request('16v100-avx2')
        self.stack.enter_context(patch.dict(os.environ))
        self.stack.enter_context(patch.object(sys, 'path', list(sys.path)))
        self.stack.enter_context(patch.object(sys, 'dont_write_bytecode', sys.dont_write_bytecode))
        for module in (build, science, performance):
            for name, value in (('ROOT', self.root), ('CONTROL', self.control), ('PROJECT', self.project)):
                self.stack.enter_context(patch.object(module, name, value))
        self.stack.enter_context(patch.object(md_tracking, 'fingerprint', return_value=self.delivery['recipe_sha256']))
        self.image = build.artifact_path(self.root, self.delivery, self.run_id)
        self.image.parent.mkdir(parents=True)
        self.image.write_bytes(b'protocol fixture, not a real SIF')
        self.build_task = self.root / 'runs' / self.run_id
        self.write(self.build_task / 'job.sbatch', '# build fixture\n')
        record = dict(run_id=self.run_id, delivery=self.delivery, controller=str(self.control),
                      job_script_sha256=checksum(self.build_task / 'job.sbatch'))
        status = dict(state='COMPLETED', exit_code='0:0', build_verified=True,
                      artifact=str(self.image), artifact_sha256=checksum(self.image), delivery=self.delivery)
        self.write_json(self.build_task / 'request.json', record)
        self.write_json(self.build_task / 'results/status.json', status)
        self.write_json(self.image.with_suffix('.json'), status)

    @staticmethod
    def write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def write_json(self, path, value):
        self.write(path, json.dumps(value, sort_keys=True))

    def task(self, stage):
        return self.root / 'runtime-tests' / (self.run_id + '-' + stage)

    def submit(self, stage):
        directory = self.task(stage)
        request = dict(run_id=directory.name, build_run=self.run_id, delivery=self.delivery,
                       artifact=str(self.image), artifact_sha256=checksum(self.image),
                       target=self.delivery['plan']['target'])
        if stage == 'science':
            request.update(nodes=2, ranks=4, sources=self.delivery['plan']['sources'],
                           version=self.delivery['plan']['version'],
                           acceptance_recipe_sha256=self.delivery['recipe_sha256'])
            controller = science
        else:
            request.update(science_run=self.run_id + '-science', resources=performance.RESOURCES,
                           recipe_sha256=self.delivery['recipe_sha256'])
            controller = performance
            if stage == 'performance-retest':
                request['repeat_of'] = dict(run_id=self.run_id + '-performance', node='node1')
        self.write(directory / 'job.sbatch', controller.render(request, directory))
        request['job_script_sha256'] = checksum(directory / 'job.sbatch')
        self.write_json(directory / 'request.json', request)
        self.write(directory / 'job.id', '123\n')
        return request

    def existing(self, stage):
        return md_ci.inspect_existing_acceptance(self.root, self.control, self.run_id, stage, self.delivery)

    def completed(self, stage):
        directory = self.task(stage)
        self.write_json(directory / 'results/evidence.json', {'synthetic': stage, 'passed': stage != 'performance'})
        self.write_json(directory / 'results/status.json', dict(job='123', state='COMPLETED', exit_code='0:0',
                       evidence_sha256=checksum(directory / 'results/evidence.json')))

    def test_preflight_absent_and_existing_stages_have_no_mutations(self):
        for stage in ('science', 'performance', 'performance-retest'):
            with self.subTest(stage=stage):
                self.assertFalse(self.existing(stage))
                self.assertFalse(self.task(stage).exists())
                self.submit(stage)
                before = {path: path.read_bytes() for path in self.task(stage).rglob('*') if path.is_file()}
                self.assertTrue(self.existing(stage))
                self.assertEqual(before, {path: path.read_bytes() for path in self.task(stage).rglob('*') if path.is_file()})

    def test_incomplete_submission_never_resubmits(self):
        self.submit('science')
        (self.task('science') / 'job.id').unlink()
        remote = Mock()
        with self.assertRaisesRegex(ValueError, 'recorded Slurm handle'):
            md_ci.run_acceptance_gate(remote, self.run_id, self.existing)
        remote.assert_not_called()

    def test_mismatched_identity_script_or_job_is_rejected(self):
        original = self.submit('science')
        path = self.task('science') / 'request.json'
        for change in ({'build_run': 'other'}, {'run_id': 'other-science'}, {'target': '4v100-avx512'},
                       {'artifact_sha256': '0' * 64}, {'artifact': str(self.image) + '.other'},
                       {'acceptance_recipe_sha256': '0' * 64}, {'nodes': 1, 'ranks': 2},
                       {'delivery': delivery_request('4v100-avx512')}, {'job_script_sha256': '0' * 64}):
            self.write_json(path, dict(original, **change))
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.existing('science')
        self.write_json(path, original)
        job = self.task('science') / 'job.id'
        job.write_text('invalid')
        with self.assertRaisesRegex(ValueError, 'Slurm handle'):
            self.existing('science')
        job.write_text('123')
        script = self.task('science') / 'job.sbatch'
        script.write_text(script.read_text() + '# changed\n')
        self.write_json(path, dict(original, job_script_sha256=checksum(script)))
        with self.assertRaisesRegex(ValueError, 'script changed'):
            self.existing('science')

    def test_changed_artifact_snapshot_or_build_record_fails_before_monitor(self):
        self.submit('science')
        with patch.object(md_tracking, 'fingerprint', return_value='0' * 64):
            with self.assertRaisesRegex(ValueError, 'snapshot recipe'):
                self.existing('science')
        path = self.build_task / 'request.json'
        original = json.loads(path.read_text())
        self.write_json(path, dict(original, controller=str(self.root / 'controller/other')))
        with self.assertRaisesRegex(ValueError, 'verified immutable handoff'):
            self.existing('science')
        self.write_json(path, original)
        self.image.write_bytes(b'changed fixture')
        with self.assertRaisesRegex(ValueError, 'image or build identity'):
            self.existing('science')

    def test_performance_repeat_name_and_science_binding_cannot_change(self):
        original = self.submit('performance-retest')
        path = self.task('performance-retest') / 'request.json'
        for change in ({'science_run': 'other-science'}, {'repeat_of': {'run_id': 'other-performance'}},
                       {'resources': dict(performance.RESOURCES, ranks=2)}, {'repeat_of': None}):
            self.write_json(path, dict(original, **change))
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.existing('performance-retest')
        with self.assertRaisesRegex(ValueError, 'unknown MD acceptance'):
            self.existing('performance-retest-again')

    def test_rerun_after_publication_failure_reuses_existing_successful_repeat(self):
        submissions, observations, publications = [], [], []
        fail_publish = True
        def remote(script, action, run_id, *args):
            nonlocal fail_publish
            if action == 'submit':
                stage = run_id.removeprefix(self.run_id + '-')
                self.assertFalse(self.task(stage).exists(), 'duplicate stage submission')
                self.submit(stage)
                submissions.append(stage)
            elif action in ('monitor', 'verify') and script != 'md_publication.py':
                observations.append(run_id)
                if action == 'monitor':
                    self.completed(run_id.removeprefix(self.run_id + '-'))
                if run_id.endswith('-performance'):
                    raise subprocess.CalledProcessError(3, 'verified regression')
            elif action == 'publish':
                publications.append(args[-1])
                if fail_publish:
                    fail_publish = False
                    raise subprocess.CalledProcessError(255, 'interrupted publication transport')
        with self.assertRaises(subprocess.CalledProcessError):
            md_ci.run_acceptance_gate(remote, self.run_id, self.existing)
        self.assertEqual(submissions, ['science', 'performance', 'performance-retest'])
        pinned = {path: path.read_bytes() for path in (self.root / 'runtime-tests').rglob('*.json')}
        result = md_ci.run_acceptance_gate(remote, self.run_id, self.existing)
        self.assertEqual(result, 'build-performance-retest')
        self.assertEqual(submissions, ['science', 'performance', 'performance-retest'])
        self.assertEqual(observations, ['build-science', 'build-performance', 'build-performance-retest'] * 2)
        self.assertEqual(publications, ['build-performance-retest'] * 2)
        self.assertEqual(pinned, {path: path.read_bytes() for path in (self.root / 'runtime-tests').rglob('*.json')})

    def test_completed_regression_verifies_without_rewriting_original_receipts(self):
        for stage in ('science', 'performance', 'performance-retest'):
            self.submit(stage)
            self.completed(stage)
            self.assertEqual(self.existing(stage), 'completed')
        def verify(script, action, run_id, *args):
            self.assertIn(action, ('verify', 'publish'))
            if script == 'md_performance_controller.py' and run_id == 'build-performance':
                raise subprocess.CalledProcessError(3, 'verified slowdown')
        remote = Mock(side_effect=verify)
        self.assertEqual(md_ci.run_acceptance_gate(remote, self.run_id, self.existing), 'build-performance-retest')
        self.assertEqual([call.args[1] for call in remote.call_args_list], ['verify'] * 3 + ['publish', 'verify'])
        evidence = self.task('performance') / 'results/evidence.json'
        evidence.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'completed receipt'):
            self.existing('performance')

    def test_embedded_preflight_executes_as_readonly_python_subprocess(self):
        source = Path(md_ci.__file__).parent
        for path in source.glob('*'):
            if path.is_file():
                shutil.copy2(path, self.control / path.name)
        # The test patches the in-process fingerprint; the isolated process
        # must instead validate the real copied snapshot's bytes.
        recipe = subprocess.check_output([sys.executable, '-c',
            'import sys; sys.path.insert(0, sys.argv[1]); from md_tracking import fingerprint; print(fingerprint(sys.argv[1]))',
            str(self.control)], text=True).strip()
        self.delivery = delivery_request('16v100-avx2', recipe=recipe)
        self.image = build.artifact_path(self.root, self.delivery, self.run_id)
        self.image.parent.mkdir(parents=True)
        self.image.write_bytes(b'isolated process metadata fixture')
        record = dict(run_id=self.run_id, delivery=self.delivery, controller=str(self.control),
                      job_script_sha256=checksum(self.build_task / 'job.sbatch'))
        status = dict(state='COMPLETED', exit_code='0:0', build_verified=True, delivery=self.delivery,
                      artifact=str(self.image), artifact_sha256=checksum(self.image))
        self.write_json(self.build_task / 'request.json', record)
        self.write_json(self.build_task / 'results/status.json', status)
        self.write_json(self.image.with_suffix('.json'), status)
        self.submit('science')
        self.completed('science')
        def ssh(command, **kwargs):
            return subprocess.run(command, check=True, text=True, **kwargs)
        before = {path: path.read_bytes() for path in self.task('science').rglob('*') if path.is_file()}
        self.assertEqual(md_ci.existing_acceptance(ssh, str(self.root), str(self.control),
                                                   self.run_id, 'science', self.delivery), 'completed')
        self.assertEqual(before, {path: path.read_bytes() for path in self.task('science').rglob('*') if path.is_file()})

    def test_existing_failure_or_second_regression_still_blocks_publication(self):
        for stage in ('science', 'performance', 'performance-retest'):
            self.submit(stage)
        for fail_run, code in (('build-science', 1), ('build-performance', 1), ('build-performance-retest', 3)):
            calls = []
            def remote(script, action, run_id, *args):
                calls.append((action, run_id))
                if action == 'monitor' and (run_id == fail_run or run_id == 'build-performance'):
                    raise subprocess.CalledProcessError(code if run_id == fail_run else 3, 'monitor')
            with self.subTest(fail_run=fail_run), self.assertRaises(subprocess.CalledProcessError):
                md_ci.run_acceptance_gate(remote, self.run_id, self.existing)
            self.assertFalse(any(action in ('submit', 'publish') for action, _ in calls))

    def test_remote_preflight_serialization_rejects_nonboolean_response(self):
        transport = Mock(return_value=subprocess.CompletedProcess([], 0, 'true'))
        self.assertTrue(md_ci.existing_acceptance(transport, str(self.root), str(self.control),
                                                  self.run_id, 'science', self.delivery))
        command = transport.call_args.args[0]
        self.assertEqual(command[:2], ['python3', '-c'])
        self.assertIn('def inspect_existing_acceptance', command[2])
        self.assertEqual(command[3:7], [str(self.root), str(self.control), self.run_id, 'science'])
        self.assertEqual(json.loads(command[7]), self.delivery)
        transport.return_value.stdout = '1'
        with self.assertRaisesRegex(ValueError, 'invalid MD continuation'):
            md_ci.existing_acceptance(transport, str(self.root), str(self.control), self.run_id, 'science', self.delivery)


if __name__ == '__main__':
    unittest.main()
