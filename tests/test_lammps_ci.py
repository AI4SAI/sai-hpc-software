"""Independent LAMMPS CI freezes its source and requires GPU science after build."""
from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'controller'))
import md_lammps_ci as ci
from release_contract import claim_build, resolve_tracks
import lammps_acceptance as acceptance
import test_lammps_acceptance as acceptance_tests
import yaml


def source(track='development'):
    return resolve_tracks('lammps', [track], resolver=lambda repo, ref: dict(
        sha='a' * 40, ref='develop' if track == 'development' else 'stable_test', version='develop-test'))[0]


class SourceTests(unittest.TestCase):
    def test_workflow_matrix_row_keeps_its_partition_and_frozen_source(self):
        selected = source()
        row = dict(selected, target='16v100-avx2')
        with patch.dict(os.environ, {'LAMMPS_SOURCE': json.dumps(row), 'LAMMPS_TARGET': row['target']}, clear=True):
            self.assertEqual(ci.selected_source(), selected)
        with patch.dict(os.environ, {'LAMMPS_SOURCE': json.dumps(row), 'LAMMPS_TARGET': '4v100-avx512'}, clear=True), \
                self.assertRaisesRegex(ValueError, 'matrix target'):
            ci.selected_source()

    def test_matrix_source_is_used_without_resolving_the_moving_branch_again(self):
        selected = source()
        with patch.dict(os.environ, {'LAMMPS_SOURCE': json.dumps(selected)}, clear=True):
            self.assertEqual(ci.selected_source(), selected)
        for field, value in (('repository', 'other/repo'), ('software', 'cp2k'), ('status', 'skipped'),
                             ('source_sha', 'short'), ('source_ref', 'other-branch'),
                             ('requested_ref', 'master'), ('unexpected', 'field')):
            with self.subTest(field=field), patch.dict(os.environ, {
                    'LAMMPS_SOURCE': json.dumps(dict(selected, **{field: value}))}, clear=True), \
                    self.assertRaises(ValueError):
                ci.selected_source()


class LifecycleTests(unittest.TestCase):
    def exercise(self, runner, history, *, track='development', attempt='1', retry='false', failure=None,
                 phase='all', handoff=None, state=None):
        commands, uploads = [], []
        state = {} if state is None else state
        runner.mkdir(parents=True, exist_ok=True)
        environment = dict(LAMMPS_SOURCE=json.dumps(dict(source(track), target='16v100-avx2')), LAMMPS_TARGET='16v100-avx2',
            GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT=attempt, GITHUB_SHA='b' * 40,
            REMOTE_USER='unit', RUNNER_TEMP=str(runner), RETRY_RELEASES=retry, LAMMPS_PHASE=phase)
        if handoff:
            environment['LAMMPS_HANDOFF'] = str(handoff)
        def remote(argv, **kwargs):
            if argv[0] == 'scp':
                uploads.append(list(map(str, argv)))
                return subprocess.CompletedProcess(argv, 0, '')
            command = shlex.split(argv[-1])
            if command[0] == 'env':
                self.assertEqual(command[1], 'SAI_SOFTWARE_ROOT=/org/org-open-sai/proj-sai-hpc-software')
                command = command[2:]
            if command[0] == 'python3':
                name = Path(command[1]).name
                commands.append((name, command[2:]))
                if name == 'release_contract.py':
                    self.assertEqual(len(uploads), 1)
                    decision = claim_build(history, json.loads(command[4]), command[3], retry='--retry' in command[5:])
                    return subprocess.CompletedProcess(argv, 0, json.dumps(decision))
                if name == 'source_cache.py':
                    return subprocess.CompletedProcess(argv, 0, json.dumps({'cache_shas': [command[-1]]}))
                if (name, command[2]) == failure:
                    raise subprocess.CalledProcessError(1, argv)
                if name == 'lammps_acceptance.py' and command[2] == 'submit':
                    self.assertFalse(state.get('science_exists'), 'duplicate scientific submission')
                    state['science_exists'] = True
            return subprocess.CompletedProcess(argv, 0, '')
        def preflight(ssh, project, marker, *, continuing):
            self.assertEqual(marker['source'], source(track))
            if continuing:
                self.assertEqual(marker, json.loads((runner / 'results/ci-handoff.json').read_text()))
            status = dict(job='1001', delivery=marker['delivery'], state='COMPLETED', exit_code='0:0',
                          artifact=project + '/experimental/deepmd-lammps/runs/' + marker['run_id'] + '/candidate.sif',
                          artifact_sha256='c' * 64, build_verified=True)
            return dict(build_status=status if marker['decision']['build'] else None,
                        science_exists=state.get('science_exists', False))
        output = io.StringIO()
        with patch.dict(os.environ, environment, clear=True), patch.object(ci, 'run', side_effect=remote), \
                patch.object(ci, 'remote_handoff', side_effect=preflight), \
                patch.object(ci.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)), redirect_stdout(output):
            if failure:
                with self.assertRaises(subprocess.CalledProcessError):
                    ci.main()
            else:
                ci.main()
        return commands, uploads, output.getvalue()

    def test_build_then_fourteen_case_science_and_raw_verification_are_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands, uploads, output = self.exercise(root, root / 'history')
            self.assertEqual([name for name, args in commands[:3]], ['release_contract.py', 'source_cache.py', 'source_cache.py'])
            actions = [(name, args[0]) for name, args in commands[3:]]
            self.assertEqual(actions, [('md_lammps_standalone.py', 'submit'), ('md_lammps_standalone.py', 'monitor'),
                ('lammps_acceptance.py', 'submit'), ('lammps_acceptance.py', 'monitor'), ('lammps_acceptance.py', 'verify')])
            run_id = commands[0][1][1]
            self.assertIn('-development-16v100-avx2-', run_id)
            self.assertTrue(run_id.endswith('-aaaaaaaaaaaa'))
            self.assertEqual(len(uploads), 2)
            for name in ('lammps_acceptance.py', 'lammps_rank.sh', 'lammps_verify.sbatch',
                         'native-reference.json', 'runtime-plan.json', 'delivery.json'):
                self.assertTrue(any(Path(arg).name == name for arg in uploads[0][:-1]))
            self.assertIn('LAMMPS_GPU_SCIENCE_ACCEPTED', output)

    def test_separate_science_job_and_rerun_use_original_build_without_transport_or_reclaim(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = {}
            commands, _, output = self.exercise(root / 'build', root / 'history', phase='build')
            self.assertFalse(any(name == 'lammps_acceptance.py' for name, _ in commands))
            self.assertNotIn('GPU_SCIENCE_ACCEPTED', output)
            handoff = root / 'build/results/ci-handoff.json'
            original = handoff.read_bytes()
            marker = json.loads(original)
            # A changed date/attempt must never derive a new remote run id.
            with patch.object(ci, 'make_delivery', side_effect=AssertionError('science must reuse original delivery')), \
                    patch.object(ci, 'datetime') as clock:
                clock.now.side_effect = AssertionError('science must reuse original run id')
                for attempt, expected in (('2', ['submit', 'monitor', 'verify']), ('3', ['monitor', 'verify'])):
                    commands, uploads, output = self.exercise(root / ('science-' + attempt), root / 'history',
                        phase='science', handoff=handoff, attempt=attempt, state=state)
                    self.assertEqual([(name, args[0]) for name, args in commands],
                                     [('lammps_acceptance.py', action) for action in expected])
                    self.assertFalse(uploads)
                    for _, args in commands:
                        self.assertIn(marker['run_id'], args[1])
                    self.assertIn('LAMMPS_GPU_SCIENCE_ACCEPTED', output)
            self.assertEqual(handoff.read_bytes(), original)

    def test_science_monitor_failure_is_resumed_without_new_submission(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.exercise(root / 'build', root / 'history', phase='build')
            handoff, state = root / 'build/results/ci-handoff.json', {}
            _, _, output = self.exercise(root / 'science', root / 'history', phase='science', handoff=handoff,
                state=state, failure=('lammps_acceptance.py', 'monitor'))
            self.assertNotIn('GPU_SCIENCE_ACCEPTED', output)
            commands, _, _ = self.exercise(root / 'rerun', root / 'history', phase='science',
                                           handoff=handoff, state=state, attempt='2')
            self.assertEqual([args[0] for _, args in commands], ['monitor', 'verify'])

    def test_unchanged_release_skips_before_source_transport_and_retry_is_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.exercise(root, root / 'history', track='release')
            commands, _, output = self.exercise(root, root / 'history', track='release', attempt='2')
            self.assertEqual([name for name, args in commands], ['release_contract.py'])
            self.assertIn('UNCHANGED_RELEASE_SKIPPED', output)
            self.assertNotIn('GPU_SCIENCE_ACCEPTED', output)
            commands, _, _ = self.exercise(root, root / 'history', track='release', attempt='3', retry='true')
            self.assertEqual(commands[-1][0], 'lammps_acceptance.py')
            self.assertEqual(commands[-1][1][0], 'verify')

    def test_unchanged_release_handoff_skips_science_without_new_acceptance_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.exercise(root / 'first', root / 'history', phase='build', track='release')
            self.exercise(root / 'skip', root / 'history', phase='build', track='release', attempt='2')
            commands, uploads, output = self.exercise(root / 'science', root / 'history', phase='science',
                track='release', attempt='3', handoff=root / 'skip/results/ci-handoff.json')
            self.assertFalse(commands or uploads)
            self.assertIn('UNCHANGED_RELEASE_SKIPPED', output)
            self.assertNotIn('GPU_SCIENCE_ACCEPTED', output)

    def test_build_or_scientific_failure_never_reports_accepted(self):
        for failure in (('md_lammps_standalone.py', 'monitor'), ('lammps_acceptance.py', 'verify')):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                commands, _, output = self.exercise(root, root / 'history', failure=failure)
                self.assertNotIn('LAMMPS_GPU_SCIENCE_ACCEPTED', output)
                if failure[0] == 'md_lammps_standalone.py':
                    self.assertFalse(any(name == 'lammps_acceptance.py' for name, args in commands))

    def test_downloaded_handoff_cannot_change_source_target_controller_build_or_skip_decision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.exercise(root / 'build', root / 'history', phase='build')
            marker = json.loads((root / 'build/results/ci-handoff.json').read_text())
            def change(field, value):
                changed = copy.deepcopy(marker)
                destination = changed
                names = field.split('.')
                for name in names[:-1]:
                    destination = destination[name]
                destination[names[-1]] = value
                return changed
            environment = dict(GITHUB_RUN_ID='123', GITHUB_SHA='b' * 40, GITHUB_RUN_ATTEMPT='2')
            with patch.dict(os.environ, environment, clear=True):
                self.assertEqual(ci.validate_handoff(marker, source(), '16v100-avx2'), marker)
                for field, value in (('workflow_run_id', '456'), ('controller_sha', 'd' * 40), ('schema', True),
                        ('source.source_sha', 'e' * 40), ('delivery.identity.target', '4v100-avx512'),
                        ('run_id', marker['run_id'].replace('-1-development-', '-9-development-')),
                        ('run_id', marker['run_id'].replace('-16v100-avx2-', '-4v100-avx512-')),
                        ('build_status.artifact', '/other/candidate.sif'), ('build_status.build_verified', False),
                        ('build_status.artifact_sha256', 'bad'), ('build_status', None),
                        ('decision.build', False), ('decision.identities', []), ('decision.retry', 'false'),
                        ('controller_files.native-reference.json', '0' * 64)):
                    # File names contain a dot, so handle that dictionary directly.
                    if field.startswith('controller_files.'):
                        altered = copy.deepcopy(marker)
                        altered['controller_files']['native-reference.json'] = value
                    else:
                        altered = change(field, value)
                    with self.subTest(field=field), self.assertRaises(ValueError):
                        ci.validate_handoff(altered, source(), '16v100-avx2')

    def test_science_without_frozen_source_fails_before_resolution_or_transport(self):
        with patch.dict(os.environ, {'LAMMPS_PHASE': 'science'}, clear=True), \
                patch.object(ci, 'resolve_tracks') as resolver, patch.object(ci, 'run') as transport, \
                self.assertRaisesRegex(ValueError, 'original frozen matrix'):
            ci.main()
        resolver.assert_not_called()
        transport.assert_not_called()


class ContinuationPreflightTests(unittest.TestCase):
    def setUp(self):
        fixture = acceptance_tests.BuildProvenanceTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.project, self.root, self.build, self.status = fixture.project, fixture.root, fixture.build, fixture.status
        self.control = self.root / 'controller' / ('b' * 40) / 'build'
        self.control.parent.mkdir(parents=True)
        Path(fixture.request['controller']).rename(self.control)
        fixture.request['controller'] = str(self.control)
        self.delivery = ci.make_delivery(source(), '16v100-avx2')
        fixture.request['delivery'] = self.delivery
        self.status['delivery'] = self.delivery
        self.write(self.build / 'request.json', fixture.request)
        self.write(self.build / 'results/status.json', self.status)
        self.write(self.control / 'delivery.json', self.delivery)
        for name in ('lammps_acceptance.py', 'lammps_rank.sh', 'lammps_verify.sbatch', 'remote_controller.py'):
            shutil.copyfile(REPO / 'controller' / name, self.control / name)
        fixture.stack.enter_context(patch.object(acceptance, 'PACKAGE', self.control))
        fixture.stack.enter_context(patch.dict(os.environ))
        fixture.stack.enter_context(patch.object(sys, 'path', list(sys.path)))
        fixture.stack.enter_context(patch.object(sys, 'dont_write_bytecode', sys.dont_write_bytecode))
        self.decision = claim_build(self.root, [self.delivery['identity']], 'build')
        self.marker = dict(controller_sha='b' * 40, run_id='build', delivery=self.delivery,
            decision=self.decision, build_status=self.status,
            controller_files={path.name: acceptance.digest(path) for path in self.control.iterdir()})
        self.write(self.control / 'ci-handoff.json', self.marker)
        self.output = self.root / 'runtime-tests/build-science'
        self.fixture = fixture

    @staticmethod
    def write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, sort_keys=True))

    def probe(self):
        return ci.inspect_handoff(str(self.project), self.marker, True)

    def submit(self):
        # Real frozen request construction with a local fixture input. These
        # metadata tests do not claim to run the scientific cases or Slurm.
        reference = self.project / 'native-reference.json'
        self.write(reference, dict(partitions={'16V100': dict(tools={}, case_files={}, gpu={}, science_case='.')}))
        with patch.object(acceptance, 'REFERENCE_SHA256', acceptance.digest(reference)), \
                patch.object(acceptance, 'build_status', return_value=self.status), \
                patch.object(acceptance.subprocess, 'run'), \
                patch.object(acceptance.subprocess, 'check_output', return_value='1002'), redirect_stdout(io.StringIO()):
            acceptance.submit(self.build, reference, expected_delivery=self.delivery)
        self.fixture.stack.enter_context(patch.object(acceptance, 'REFERENCE_SHA256', acceptance.digest(reference)))

    def snapshot(self):
        return {str(path.relative_to(self.project)): path.read_bytes()
                for path in self.project.rglob('*') if path.is_file()}

    def test_absent_or_existing_science_preflight_is_readonly_and_reuses_real_context(self):
        before = self.snapshot()
        self.assertEqual(self.probe(), dict(build_status=self.status, science_exists=False))
        self.assertEqual(before, self.snapshot())
        self.submit()
        before = self.snapshot()
        self.assertEqual(self.probe(), dict(build_status=self.status, science_exists=True))
        self.assertEqual(before, self.snapshot())
        self.assertFalse(any(self.project.rglob('__pycache__')))

    def test_incomplete_directory_or_missing_handle_is_ambiguous_and_never_reusable(self):
        self.output.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'incomplete previous submission'):
            self.probe()
        self.output.rmdir()
        self.submit()
        (self.output / 'job.id').unlink()
        with self.assertRaisesRegex(ValueError, 'incomplete previous submission'):
            self.probe()
        (self.output / 'job.id').write_text('invalid')
        with self.assertRaisesRegex(ValueError, 'Slurm handle'):
            self.probe()

    def test_frozen_build_image_request_controller_or_handoff_tampering_fails_closed(self):
        cases = (self.build / 'candidate.sif', self.build / 'request.json', self.control / 'lammps_rank.sh',
                 self.control / 'ci-handoff.json', self.build / 'input/build-attempt.json')
        for path in cases:
            original = path.read_bytes()
            try:
                path.write_bytes(b'{}' if path.suffix == '.json' else original + b'changed')
                with self.subTest(path=path), self.assertRaises((ValueError, KeyError)):
                    self.probe()
            finally:
                path.write_bytes(original)

    def test_science_receipt_tampering_or_rebound_build_is_rejected(self):
        self.submit()
        request = self.output / 'request.json'
        original = json.loads(request.read_text())
        for changed in (dict(original, build=str(self.build) + '-other'),
                        dict(original, build_request_sha256='0' * 64),
                        dict(original, reference_root='/other'),
                        dict(original, build_status=dict(self.status, artifact_sha256='0' * 64))):
            self.write(request, changed)
            (self.output / 'request.sha256').write_text(acceptance.digest(request))
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, 'science differs'):
                self.probe()
        self.write(request, original)
        (self.output / 'request.sha256').write_text('0' * 64)
        with self.assertRaisesRegex(ValueError, 'frozen acceptance request changed'):
            self.probe()

    def test_redirected_science_or_metadata_is_rejected(self):
        self.submit()
        handle = self.output / 'job.id'
        destination = self.project / 'other-handle'
        handle.rename(destination)
        handle.symlink_to(destination)
        with self.assertRaisesRegex(ValueError, 'redirected metadata'):
            self.probe()
        handle.unlink()
        destination.rename(handle)
        moved = self.output.with_name('other-science')
        self.output.rename(moved)
        self.output.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'continuation path'):
            self.probe()

    def test_remote_serialization_runs_in_isolated_interpreter_with_frozen_helpers(self):
        # The subprocess uses the real acceptance controller and frozen delivery
        # stub, with only the scheduler replaced by a deterministic local fixture.
        scheduler = self.project / 'bin/sacct'
        scheduler.parent.mkdir()
        scheduler.write_text('#!/bin/sh\nprintf "%s\\n" ' + shlex.quote('|'.join(self.fixture.row)) + '\n')
        scheduler.chmod(0o700)
        def local_ssh(argv, **kwargs):
            return subprocess.run(argv, check=True, text=True, env=dict(os.environ,
                                  PATH=str(scheduler.parent) + ':' + os.defpath), **kwargs)
        self.assertEqual(ci.remote_handoff(local_ssh, str(self.project), self.marker, continuing=True),
                         dict(build_status=self.status, science_exists=False))
        self.assertFalse(any(self.project.rglob('__pycache__')))


class WorkflowPhaseTests(unittest.TestCase):
    def jobs(self):
        return yaml.safe_load((REPO / '.github/workflows/lammps.yml').read_text())['jobs']

    def test_build_and_science_use_distinct_budgets_and_per_matrix_handoff(self):
        jobs = self.jobs()
        for name, phase in (('candidate', 'build'), ('science', 'science')):
            self.assertEqual(jobs[name]['env']['LAMMPS_PHASE'], phase)
            self.assertEqual(jobs[name]['timeout-minutes'], 360)
            self.assertEqual(jobs[name]['environment'], 'hpc')
            self.assertEqual(jobs[name]['strategy']['matrix'], jobs['candidate']['strategy']['matrix'])
        upload = next(step for step in jobs['candidate']['steps'] if step.get('name') == 'Preserve immutable build handoff')
        download = next(step for step in jobs['science']['steps'] if step.get('uses') == 'actions/download-artifact@v4')
        self.assertEqual(upload['with']['name'], download['with']['name'])
        for value in ('matrix.build.track', 'matrix.build.target', 'github.run_id'):
            self.assertIn(value, upload['with']['name'])
        self.assertNotIn('github.run_attempt', upload['with']['name'])
        self.assertTrue(upload['with']['overwrite'])
        self.assertEqual(upload['with']['if-no-files-found'], 'error')
        self.assertIn('!cancelled()', jobs['science']['if'])
        self.assertNotIn('needs.candidate.result', jobs['science']['if'])

    def test_result_requires_both_matrices_and_allows_empty_channel_or_validation_only(self):
        jobs = self.jobs()
        self.assertEqual(set(jobs['result']['needs']), {'validate', 'resolve-source', 'candidate', 'science'})
        code = jobs['result']['steps'][0]['run'].split("python3 - <<'PYTHON'\n", 1)[1].rsplit('\nPYTHON', 1)[0]
        def gate(results, event='schedule'):
            with patch.dict(os.environ, RESULTS=json.dumps(results), GITHUB_EVENT_NAME=event):
                exec(compile(code, 'lammps.yml', 'exec'), {})
        selected = {'validate': {'result': 'success'},
                    'resolve-source': {'result': 'success', 'outputs': {'builds': '[{}]'}},
                    'candidate': {'result': 'success'}, 'science': {'result': 'success'}}
        gate(selected)
        for phase in ('candidate', 'science'):
            for result in ('failure', 'cancelled', 'skipped'):
                changed = copy.deepcopy(selected)
                changed[phase]['result'] = result
                with self.subTest(phase=phase, result=result), self.assertRaises(SystemExit):
                    gate(changed)
        empty = copy.deepcopy(selected)
        empty['resolve-source']['outputs']['builds'] = '[]'
        empty['candidate']['result'] = empty['science']['result'] = 'skipped'
        gate(empty)
        empty['resolve-source']['result'] = 'skipped'
        for event in ('push', 'pull_request'):
            gate(empty, event)


if __name__ == '__main__':
    unittest.main()
