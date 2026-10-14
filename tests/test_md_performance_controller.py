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
            raw_report = dict(files={}, performance=dict(candidate={'median_engine_seconds': 11.01},
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


if __name__ == '__main__':
    unittest.main()
