"""Execute workflow outcome gates against failures, cancellation and deduplication."""
import json
import os
from pathlib import Path
import tempfile
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


class SelectedBuildOutcomeTests(unittest.TestCase):
    def selected(self, name, builds='[{}]'):
        resolve, build = ('resolve', 'candidate-build') if name == 'deepmd-lammps' else ('resolve-source', 'build')
        return {'validate': {'result': 'success'},
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
        return {'validate': {'result': 'success'},
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
