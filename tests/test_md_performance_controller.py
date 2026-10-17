"""Lifecycle/provenance tests with synthetic records; no simulated scientific acceptance."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'controller'))
import md_ci
import md_performance_controller as controller
from test_md_controller import delivery_request


class PerformanceLifecycleTests(unittest.TestCase):
    def test_first_success_publishes_no_second_trial(self):
        remote = Mock()
        accepted = md_ci.run_performance_gate(remote, 'build1', 'science1')
        self.assertEqual(accepted, 'build1-performance')
        self.assertEqual(remote.call_count, 2)
        self.assertEqual(remote.call_args_list[0].args,
                         ('md_performance_controller.py', 'submit', 'build1-performance', 'build1', 'science1'))

    def test_verified_regression_repeats_same_candidate_once(self):
        remote = Mock(side_effect=[None, subprocess.CalledProcessError(3, 'monitor'), None, None])
        self.assertEqual(md_ci.run_performance_gate(remote, 'build1', 'science1'), 'build1-performance-retest')
        self.assertEqual(remote.call_count, 4)
        self.assertEqual(remote.call_args_list[2].args,
                         ('md_performance_controller.py', 'submit', 'build1-performance-retest', 'build1', 'science1'))

    def test_persistent_regression_and_unverified_failure_abort(self):
        for codes, expected_calls in (([1], 2), ([3, 3], 4), ([3, 1], 4)):
            side_effect = []
            for code in codes:
                side_effect.extend([None, subprocess.CalledProcessError(code, 'monitor')])
            remote = Mock(side_effect=side_effect)
            with self.subTest(codes=codes), self.assertRaises(subprocess.CalledProcessError):
                md_ci.run_performance_gate(remote, 'build1', 'science1')
            self.assertEqual(remote.call_count, expected_calls)

    def test_three_partitions_have_fixed_benchmark_resources(self):
        for target in ('16v100-avx2', '4v100-avx512', '8v100v0-avx512'):
            delivery = delivery_request(target=target)
            request = dict(run_id='performance1', build_run='build1', science_run='science1',
                           delivery=delivery, artifact='/safe/candidate.sif', resources=controller.RESOURCES)
            script = controller.render(request, Path('/safe/performance1'))
            subprocess.run(['bash', '-n'], input=script, text=True, check=True)
            self.assertIn('#SBATCH --nodes=1', script)
            self.assertIn('#SBATCH --ntasks=1', script)
            self.assertIn('#SBATCH --gpus-per-node=1', script)
            self.assertIn('#SBATCH --time=120', script)
            self.assertIn('md_performance.sh performance1 science1', script)
            self.assertIn('#SBATCH --partition=' + delivery['identities']['lammps']['partition'], script)
            with self.assertRaises(ValueError):
                controller.render(dict(request, resources=dict(controller.RESOURCES, ranks=2)), Path('/safe'))

    def test_monitor_keeps_raw_regression_receipt_and_distinguishes_exit_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'results').mkdir()
            (directory / 'job.id').write_text('123\n')
            args = types.SimpleNamespace(run_id='unit', timeout=1, interval=0)
            evidence = dict(performance_verified=False, regressed_backends=['pt'])
            with patch.object(controller, 'task', return_value=directory), \
                    patch.object(controller.subprocess, 'check_output', side_effect=['', '123|COMPLETED|0:0|\n']), \
                    patch.object(controller, 'verify', return_value=evidence) as verify:
                self.assertEqual(controller.monitor(args), 3)
            verify.assert_called_once_with('unit', enforce_performance=False)
            self.assertEqual(json.loads((directory / 'results/evidence.json').read_text()), evidence)
            self.assertFalse(json.loads((directory / 'results/status.json').read_text())['performance_verified'])

    def test_all_backend_raw_results_are_required_and_slowdown_cannot_publish(self):
        import md_performance_evidence
        from source_cache import checksum
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'performance'
            scientific = root / 'science'
            for parent in (directory, scientific):
                (parent / 'case').mkdir(parents=True)
                for name in ('fixture.json', 'data.lmp', 'plumed.dat',
                             *controller.BACKENDS.values(), *(f'in.{backend}' for backend in controller.BACKENDS)):
                    (parent / 'case' / name).write_bytes(b'synthetic fixture')
            (directory / 'results').mkdir()
            (directory / 'results/nsys.path').write_text('/opt/devtools/nsys')
            (directory / 'job.id').write_text('123')
            (directory / 'job.sbatch').write_text('# synthetic job')
            (root / 'md_runtime.sh').write_text('# synthetic launcher')
            delivery = delivery_request('16v100-avx2')
            request = dict(run_id='unit', science_run='science', build_run='build', delivery=delivery,
                           artifact='/safe/image.sif', artifact_sha256='a' * 64, target='16v100-avx2',
                           recipe_sha256=delivery['recipe_sha256'],
                           job_script_sha256=checksum(directory / 'job.sbatch'))
            (directory / 'request.json').write_text(json.dumps(request))
            for backend in controller.BACKENDS:
                (directory / 'results' / backend).mkdir()
                (directory / 'results' / backend / 'frozen.json').write_text('{}')
            raw_report = dict(files={}, binaries={'baseline': 'b' * 64, 'candidate': 'c' * 64},
                              performance=dict(candidate={'median_engine_seconds': 11.01},
                                                        baseline={'median_engine_seconds': 10.0}))
            actual_is_file = Path.is_file
            def is_file(path):
                return path == Path('/opt/devtools/nsys') or actual_is_file(path)
            with patch.object(controller, 'task', return_value=directory), \
                    patch.object(controller, 'validate_request', return_value=delivery), \
                    patch.object(controller, 'science_evidence'), \
                    patch.object(controller.science, 'task', return_value=scientific), \
                    patch.object(controller, 'CONTROL', root), patch.object(Path, 'is_file', is_file), \
                    patch.object(md_performance_evidence, 'verify_performance_evidence', return_value=raw_report) as raw:
                evidence = controller.verify('unit', enforce_performance=False)
                self.assertFalse(evidence['performance_verified'])
                self.assertEqual(evidence['regressed_backends'], ['tf', 'pt', 'jax'])
                self.assertEqual(raw.call_count, 3)
                with self.assertRaises(controller.PerformanceRegression):
                    controller.verify('unit')
                raw.side_effect = ValueError('raw forces changed')
                with self.assertRaisesRegex(ValueError, 'raw forces changed'):
                    controller.verify('unit')

    def test_failed_slurm_job_cannot_become_performance_regression(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'results').mkdir()
            (directory / 'job.id').write_text('123\n')
            with patch.object(controller, 'task', return_value=directory), \
                    patch.object(controller.subprocess, 'check_output', side_effect=['', '123|FAILED|1:0|\n']), \
                    patch.object(controller, 'verify') as verify:
                self.assertEqual(controller.monitor(types.SimpleNamespace(run_id='unit', timeout=1, interval=0)), 1)
            verify.assert_not_called()

class RepeatBindingTests(unittest.TestCase):
    def setUp(self):
        from source_cache import checksum
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        patcher = patch.object(controller, 'ROOT', self.root)
        patcher.start(); self.addCleanup(patcher.stop)
        self.original_id = 'build-performance'
        self.repeat_id = self.original_id + '-retest'
        self.original = dict(run_id=self.original_id, build_run='build', science_run='science',
                             delivery={'locked': 'pair'}, artifact='/safe/image.sif', artifact_sha256='a' * 64,
                             target='16v100-avx2', resources=controller.RESOURCES, recipe_sha256='b' * 64)
        self.reports = {backend: dict(binaries={'baseline': 'c' * 64, 'candidate': 'd' * 64},
                                     fixture_sha256='e' * 64,
                                     performance={'device': [backend, 'gpu' if backend == 'pt' else 'cpu', 'node1']})
                        for backend in controller.BACKENDS}
        self.evidence = dict(passed=False, performance_verified=False, job='123',
                             regressed_backends=['pt'], reports=self.reports)
        original = controller.task(self.original_id)
        self.write(original / 'request.json', self.original)
        self.write(original / 'results/evidence.json', self.evidence)
        self.write(original / 'results/status.json', dict(state='COMPLETED', exit_code='0:0', job='123',
                   performance_verified=False, evidence_sha256=checksum(original / 'results/evidence.json')))
        for backend in controller.BACKENDS:
            frozen = dict(schema=1, workload='fixed scientific workload', backend=backend, atoms=2058,
                          steps=100, node='node1', resources=controller.RESOURCES, fixture_sha256='e' * 64,
                          input_sha256='f' * 64, input='fixed input', minimum_engine_seconds=3.0)
            self.write(original / 'results' / backend / 'frozen.json', frozen)
        self.repeat = dict(self.original, run_id=self.repeat_id, repeat_of=controller.repeat_binding(self.original_id))
        for backend in controller.BACKENDS:
            frozen = json.loads((original / 'results' / backend / 'frozen.json').read_text())
            frozen['repeat_of'] = {'frozen_sha256': self.repeat['repeat_of']['frozen_sha256'][backend]}
            self.write(controller.task(self.repeat_id) / 'results' / backend / 'frozen.json', frozen)
        verifier = patch.object(controller, 'verify', return_value=self.evidence)
        self.verifier = verifier.start(); self.addCleanup(verifier.stop)

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True))

    def test_valid_repeat_binds_completed_original_raw_regression(self):
        result = controller.verify_repeat(self.repeat, self.reports)
        self.assertEqual(result, self.repeat['repeat_of'])
        self.verifier.assert_called_once_with(self.original_id, enforce_performance=False)

    def test_missing_or_changed_original_status_or_evidence_blocks_repeat(self):
        path = controller.task(self.original_id) / 'results/evidence.json'
        path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'original raw evidence'):
            controller.verify_repeat(self.repeat, self.reports)

    def test_repeat_cannot_be_of_a_success_or_a_previous_repeat(self):
        from source_cache import checksum
        self.evidence.update(passed=True, performance_verified=True, regressed_backends=[])
        original = controller.task(self.original_id)
        self.write(original / 'results/evidence.json', self.evidence)
        status = json.loads((original / 'results/status.json').read_text())
        status['evidence_sha256'] = checksum(original / 'results/evidence.json')
        self.write(original / 'results/status.json', status)
        with self.assertRaisesRegex(ValueError, 'greater-than-10% regression'):
            controller.verify_repeat(self.repeat, self.reports)
        with self.assertRaisesRegex(ValueError, 'first original attempt'):
            controller.original_for_repeat(dict(self.repeat, run_id=self.repeat_id + '-retest'))

    def test_changed_original_calibration_after_submission_is_rejected(self):
        path = controller.task(self.original_id) / 'results/tf/frozen.json'
        value = json.loads(path.read_text()); value['steps'] = 999
        self.write(path, value)
        with self.assertRaisesRegex(ValueError, 'changed after repeat submission'):
            controller.verify_repeat(self.repeat, self.reports)

    def test_repeat_cannot_change_frozen_steps_or_baseline_binary_or_device(self):
        import copy
        for field, replacement in (('binaries', {'baseline': '0' * 64, 'candidate': 'd' * 64}),
                                   ('fixture_sha256', '0' * 64),
                                   ('performance', {'device': ['pt', 'gpu', 'other-node']})):
            reports = copy.deepcopy(self.reports)
            reports['pt'][field] = replacement
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'binaries, scientific inputs or device'):
                controller.verify_repeat(self.repeat, reports)
        path = controller.task(self.repeat_id) / 'results/pt/frozen.json'
        value = json.loads(path.read_text()); value['steps'] += 1
        self.write(path, value)
        with self.assertRaisesRegex(ValueError, 'original frozen calibrated workload'):
            controller.verify_repeat(self.repeat, self.reports)

    def test_repeat_cannot_change_artifact_science_or_resources(self):
        for change in ({'artifact': '/other.sif'}, {'science_run': 'other-science'},
                       {'resources': dict(controller.RESOURCES, ranks=2)}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'scientific inputs, source or allocation'):
                controller.original_for_repeat(dict(self.repeat, **change))

    def test_repeat_render_pins_original_node_and_source_workloads(self):
        request = dict(self.repeat, delivery=delivery_request('16v100-avx2'))
        script = controller.render(request, Path('/safe/repeat'))
        subprocess.run(['bash', '-n'], input=script, text=True, check=True)
        self.assertIn('#SBATCH --nodelist=node1', script)
        self.assertIn('md_performance.sh build-performance-retest science build-performance', script)


if __name__ == '__main__':
    unittest.main()
