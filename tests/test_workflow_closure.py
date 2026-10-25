"""Execute workflow outcome gates against failures, cancellation and deduplication."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    return yaml.safe_load((ROOT / '.github/workflows' / (name + '.yml')).read_text())


def gate(name, results, **environment):
    job = workflow(name)['jobs']['result']
    code = job['steps'][0]['run'].split("python3 - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            'RESULTS': json.dumps(results), 'GITHUB_STEP_SUMMARY': tmp + '/summary',
            'GITHUB_EVENT_NAME': 'schedule', 'BUILD_ENABLED': 'true',
            'SOFTWARE': 'abacus', 'RELEASE_TRACK': 'development', **environment}):
        exec(compile(code, name + '.yml', 'exec'), {})


class EnvironmentCredentialTests(unittest.TestCase):
    def preparation(self, name):
        data = workflow(name)
        phases = [job for job in data['jobs'].values() if job.get('environment') == 'hpc']
        self.assertTrue(phases)
        return next(step['run'] for step in phases[0]['steps'] if 'KEY' in step.get('env', {}))

    def test_environment_only_secrets_do_not_require_caller_repository_secrets(self):
        # Environment secrets are unavailable in the caller and in the result
        # job. Required workflow_call secrets made result fail before a runner
        # started, even after successful environment-backed publication.
        for name in ('software-pair', 'md-pair'):
            data = workflow(name)
            for credential in ('REMOTE_SSH_PRIVATE_KEY', 'REMOTE_USER'):
                with self.subTest(workflow=name, credential=credential):
                    self.assertFalse(data[True]['workflow_call']['secrets'][credential]['required'])
            self.assertNotIn('environment', data['jobs']['result'])

    def test_missing_environment_credentials_fail_before_creating_key(self):
        for name in ('software-pair', 'md-pair'):
            for missing in ('REMOTE_USER', 'KEY'):
                with self.subTest(workflow=name, missing=missing), tempfile.TemporaryDirectory() as tmp:
                    environment = dict(PATH=os.defpath, RUNNER_TEMP=tmp,
                                       REMOTE_USER='unit-user', KEY='unit-private-key')
                    environment.pop(missing)
                    result = subprocess.run(['bash', '-c', self.preparation(name)],
                                            env=environment, capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('hpc Environment or caller secrets', result.stderr)
                    self.assertNotIn('unit-private-key', result.stdout + result.stderr)
                    self.assertFalse((Path(tmp) / 'ssh').exists())

    def test_resolved_environment_credentials_prepare_exact_key_without_logging_it(self):
        for name in ('software-pair', 'md-pair'):
            with self.subTest(workflow=name), tempfile.TemporaryDirectory() as tmp:
                key = 'unit-private-key\nsecond-line'
                result = subprocess.run(['bash', '-c', self.preparation(name)],
                                        env=dict(PATH=os.defpath, RUNNER_TEMP=tmp,
                                                 REMOTE_USER='unit-user', KEY=key),
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((Path(tmp) / 'ssh/key').read_text(), key + '\n')
                self.assertNotIn(key, result.stdout + result.stderr)


class SelectedBuildOutcomeTests(unittest.TestCase):
    def selected(self, name, builds='[{}]'):
        resolve, build = ('resolve', 'candidate-build') if name == 'deepmd-lammps' else ('resolve-source', 'build')
        return {'validate': {'result': 'success'}, 'beta': {'result': 'success'},
                resolve: {'result': 'success', 'outputs': {'builds': builds}},
                build: {'result': 'success' if builds != '[]' else 'skipped'}}, resolve, build

    def test_all_selected_children_fail_closed_for_missing_or_failed_builds(self):
        for name in ('build', 'gpumd', 'deepmd-lammps'):
            results, resolve, build = self.selected(name)
            gate(name, results)
            for outcome in ('failure', 'cancelled', 'skipped'):
                results[build]['result'] = outcome
                with self.subTest(workflow=name, outcome=outcome), self.assertRaises(SystemExit):
                    gate(name, results)
            results[build]['result'] = 'skipped'
            results[resolve]['result'] = 'failure'
            with self.assertRaises(SystemExit):
                gate(name, results)

    def test_beta_failure_prevents_daily_success(self):
        for name in ('build', 'gpumd'):
            results, _, _ = self.selected(name)
            for outcome in ('failure', 'cancelled', 'skipped'):
                results['beta']['result'] = outcome
                with self.subTest(workflow=name, outcome=outcome), self.assertRaises(SystemExit):
                    gate(name, results)

    def test_missing_upstream_release_is_a_successful_skip_but_validation_is_required(self):
        for name in ('build', 'gpumd', 'deepmd-lammps'):
            results, _, _ = self.selected(name, '[]')
            gate(name, results)
            results['validate']['result'] = 'failure'
            with self.assertRaises(SystemExit):
                gate(name, results)

    def test_manual_md_tracking_only_does_not_claim_failed_or_missing_builds(self):
        results, _, build = self.selected('deepmd-lammps')
        results[build]['result'] = 'skipped'
        gate('deepmd-lammps', results, BUILD_ENABLED='false', GITHUB_EVENT_NAME='workflow_dispatch')
        with self.assertRaises(SystemExit):
            gate('deepmd-lammps', results, BUILD_ENABLED='true', GITHUB_EVENT_NAME='workflow_dispatch')

    def test_push_and_pull_request_validate_without_requiring_remote_jobs(self):
        for name in ('build', 'gpumd', 'deepmd-lammps', 'cp2k'):
            results = {key: {'result': 'skipped'} for key in workflow(name)['jobs']['result']['needs']}
            results['validate']['result'] = 'success'
            for event in ('push', 'pull_request'):
                gate(name, results, GITHUB_EVENT_NAME=event)


class DeliveryPhaseOutcomeTests(unittest.TestCase):
    def delivery(self, software='abacus', run_id='123-development-16v100'):
        return {
            'dependencies': {'result': 'success' if software == 'cp2k' else 'skipped',
                             'outputs': {'run_id': run_id if software == 'cp2k' else ''}},
            'build': {'result': 'success', 'outputs': {'run_id': run_id}},
            'acceptance': {'result': 'success' if run_id else 'skipped'},
        }

    def test_build_success_without_acceptance_cannot_publish_workflow_success(self):
        for software in ('abacus', 'cp2k', 'gpumd'):
            results = self.delivery(software)
            gate('software-pair', results, SOFTWARE=software)
            for outcome in ('failure', 'cancelled', 'skipped'):
                results['acceptance']['result'] = outcome
                with self.subTest(software=software, outcome=outcome), self.assertRaises(SystemExit):
                    gate('software-pair', results, SOFTWARE=software)

    def test_acceptance_runs_after_skipped_dependency_ancestor(self):
        # Replay the scheduler state from daily run 39742021186: successful
        # ABACUS builds with run_id outputs never reached acceptance because
        # the CP2K-only dependency ancestor was skipped. GitHub applies an
        # implicit success() unless the job expression uses a status function.
        expression = workflow('software-pair')['jobs']['acceptance']['if']
        expression = expression.removeprefix('${{').removesuffix('}}').strip()
        explicit_status = re.search(r'\b(?:success|failure|cancelled|always)\s*\(', expression)
        code = expression.replace('&&', ' and ').replace('||', ' or ')
        code = re.sub(r'!(?!=)', 'not ', code)
        for software in ('abacus', 'gpumd', 'cp2k'):
            for outcome, run_id, cancelled, expected in (
                ('success', 'existing-build', False, True),
                ('success', '', False, False),
                ('success', 'existing-build', True, False),
                ('failure', 'existing-build', False, False),
                ('cancelled', 'existing-build', False, False),
                ('skipped', '', False, False),
            ):
                with self.subTest(software=software, build=outcome, run_id=run_id, cancelled=cancelled):
                    results = self.delivery(software, run_id)
                    results['build']['result'] = outcome
                    ancestors_succeeded = not cancelled and all(
                        results[phase]['result'] == 'success' for phase in ('dependencies', 'build'))
                    context = dict(
                        needs=SimpleNamespace(build=SimpleNamespace(
                            result=outcome, outputs=SimpleNamespace(run_id=run_id))),
                        success=lambda: ancestors_succeeded,
                        failure=lambda: outcome == 'failure',
                        cancelled=lambda: cancelled, always=lambda: True,
                    )
                    enabled = bool((explicit_status or ancestors_succeeded)
                                   and eval(code, {'__builtins__': {}}, context))
                    self.assertEqual(enabled, expected)

    def test_release_deduplication_is_allowed_but_development_must_rebuild(self):
        for software in ('abacus', 'cp2k', 'gpumd'):
            results = self.delivery(software, '')
            if software == 'cp2k':
                results['build']['result'] = 'skipped'
            for track in ('prerelease', 'release'):
                gate('software-pair', results, SOFTWARE=software, RELEASE_TRACK=track)
            with self.assertRaisesRegex(SystemExit, 'must rebuild'):
                gate('software-pair', results, SOFTWARE=software)

    def test_cp2k_dependency_failure_is_never_treated_as_deduplicated(self):
        for outcome in ('failure', 'cancelled', 'skipped'):
            results = self.delivery('cp2k', '')
            results['dependencies']['result'] = outcome
            results['build']['result'] = 'skipped'
            with self.subTest(outcome=outcome), self.assertRaises(SystemExit):
                gate('software-pair', results, SOFTWARE='cp2k', RELEASE_TRACK='release')

    def test_each_runner_job_carries_the_original_immutable_run_id(self):
        jobs = workflow('software-pair')['jobs']
        self.assertEqual(jobs['dependencies']['env']['CP2K_PHASE'], 'dependencies')
        self.assertEqual(jobs['build']['env']['CI_RUN_ID'], '${{ needs.dependencies.outputs.run_id }}')
        self.assertEqual(jobs['acceptance']['env']['CI_RUN_ID'], '${{ needs.build.outputs.run_id }}')
        self.assertEqual(jobs['acceptance']['env']['CI_PHASE'], 'acceptance')
        for phase in ('dependencies', 'build', 'acceptance'):
            self.assertLessEqual(jobs[phase]['timeout-minutes'], 360)
            self.assertEqual(jobs[phase]['environment'], 'hpc')
            self.assertTrue(any(step.get('id') == 'run' for step in jobs[phase]['steps']))


class Cp2kReferenceOrderingTests(unittest.TestCase):
    def results(self, gpu='[]', cpu='[]'):
        return {'validate': {'result': 'success'}, 'beta': {'result': 'success'},
                'resolve-source': {'result': 'success', 'outputs': {'gpu_builds': gpu, 'cpu_builds': cpu}},
                'build': {'result': 'success' if gpu != '[]' else 'skipped'},
                'build-cpu': {'result': 'success' if cpu != '[]' else 'skipped'}}

    def test_cpu_compiles_independently_and_gpu_matrix_can_run_in_parallel(self):
        jobs = workflow('cp2k')['jobs']
        self.assertNotIn('build', jobs['build-cpu']['needs'])
        self.assertIn("github.event_name == 'schedule'", jobs['build']['if'])
        self.assertIn("!cancelled()", jobs['build-cpu']['if'])
        self.assertNotIn('needs.build.result', jobs['build-cpu']['if'])
        self.assertEqual(jobs['build']['strategy']['max-parallel'], 3)
        self.assertEqual(jobs['build-cpu']['strategy']['max-parallel'], 1)
        for name in ('build', 'build-cpu'):
            self.assertEqual(jobs[name]['uses'], './.github/workflows/software-pair.yml')

    def test_gpu_only_cpu_only_and_both_selection_complete(self):
        for gpu, cpu in (('[{}]', '[]'), ('[]', '[{}]'), ('[{}]', '[{}]'), ('[]', '[]')):
            results = self.results(gpu, cpu)
            gate('cp2k', results)
            for name, selected in (('build', gpu), ('build-cpu', cpu)):
                if selected == '[]':
                    continue
                for outcome in ('failure', 'cancelled', 'skipped'):
                    changed = dict(results, **{name: {'result': outcome}})
                    with self.subTest(gpu=gpu, cpu=cpu, outcome=outcome), self.assertRaises(SystemExit):
                        gate('cp2k', changed)


if __name__ == '__main__':
    unittest.main()
