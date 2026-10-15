"""Recovery uses existing Slurm handles and keeps scientific retry budgets intact."""
import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'controller'))
import ci
import software_controller as controller
from delivery_layout import CONTRACT_SCHEMA, artifact_path
from release_contract import make_identity
from source_cache import checksum
import test_build_lifecycle as lifecycle


class ContinuationPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.control = self.root / 'controller/commit/run'
        shutil.copytree(ROOT / 'controller', self.control, ignore=shutil.ignore_patterns('__pycache__'))
        for executable in ('abacus', 'cp2k', 'gpumd', 'nep', 'gnep'):
            software = 'gpumd' if executable in ('nep', 'gnep') else executable
            shutil.copyfile(self.control / f'{software}_runtime.sh', self.control / executable)
        self.task = self.root / 'runtime-tests/build-science'

    def identity(self, software):
        return make_identity(software, 'development', 'master', 'a' * 40, 'v1',
                             controller.recipe_fingerprint(software, self.control), '16v100-avx2')

    def probe(self, expected, script='gpumd_acceptance.py', task=None):
        def local_ssh(argv, **kwargs):
            # Execute the exact SSH preflight in a separate interpreter with the
            # real immutable controller snapshot; no mock of its hash checks.
            return subprocess.run(argv, check=True, text=True, **kwargs)
        return ci.existing_job(local_ssh, str(self.root), str(self.control),
                               str(task or self.task), expected, script)

    def candidate(self, identity):
        image = artifact_path(self.root, identity, 'build')
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b'immutable SIF fixture')
        record = dict(identity=identity, software=identity['software'], version='v1',
                      target='16v100-avx2', source_sha='a' * 40,
                      recipe_sha256=identity['recipe_sha256'], artifact=str(image),
                      sha256=checksum(image), build_verified=True, contract_schema=CONTRACT_SCHEMA)
        image.with_suffix('.json').write_text(json.dumps(record))
        return image

    def acceptance(self):
        identity = self.identity('gpumd')
        image = self.candidate(identity)
        self.task.mkdir(parents=True)
        (self.task / 'job.id').write_text('1001\n')
        (self.task / 'job.sbatch').write_text('#!/bin/bash\n# immutable scientific invocation\n')
        expected = dict(run_id='build-science', build_run_id='build', identity=identity,
                        version='v1', target='16v100-avx2', artifact=str(image))
        request = dict(expected, launcher=str(self.control / 'gpumd'), artifact_sha256=checksum(image),
                       job_script_sha256=checksum(self.task / 'job.sbatch'),
                       launcher_sha256=checksum(self.control / 'gpumd'),
                       controller_sha256=checksum(self.control / 'gpumd_acceptance.py'),
                       science_sha256=checksum(self.control / 'gpumd_science.py'),
                       deepmd_probe_sha256=checksum(self.control / 'gpumd_deepmd_probe.py'))
        (self.task / 'request.json').write_text(json.dumps(request))
        return expected, request, image

    def test_absent_acceptance_is_new_existing_job_is_recoverable(self):
        self.assertFalse(self.probe({}))
        expected, _, _ = self.acceptance()
        self.assertTrue(self.probe(expected))
        # Status is intentionally not required: recovery covers monitor failure.
        self.assertFalse((self.task / 'results/status.json').exists())

    def test_changed_identity_job_script_image_or_controller_is_rejected(self):
        expected, request, image = self.acceptance()
        for key, value in (('build_run_id', 'another'), ('run_id', 'another'),
                           ('target', '4v100-avx512'), ('version', 'v2')):
            with self.subTest(key=key):
                (self.task / 'request.json').write_text(json.dumps(dict(request, **{key: value})))
                with self.assertRaises(subprocess.CalledProcessError):
                    self.probe(expected)
        (self.task / 'request.json').write_text(json.dumps(request))
        for path in (self.task / 'job.sbatch', image, self.control / 'gpumd_science.py'):
            original = path.read_bytes()
            path.write_bytes(original + b'changed')
            with self.subTest(path=path), self.assertRaises(subprocess.CalledProcessError):
                self.probe(expected)
            path.write_bytes(original)

    def test_missing_handle_and_redirected_metadata_never_resubmit(self):
        expected, _, _ = self.acceptance()
        handle = self.task / 'job.id'
        handle.unlink()
        with self.assertRaises(subprocess.CalledProcessError):
            self.probe(expected)
        other = self.root / 'foreign-job'
        other.write_text('1001')
        handle.symlink_to(other)
        with self.assertRaises(subprocess.CalledProcessError):
            self.probe(expected)

    def test_cp2k_application_checks_original_dependency_and_rendered_script(self):
        identity = self.identity('cp2k')
        task = self.root / 'runs/build'
        (task / 'input').mkdir(parents=True)
        expected = dict(run_id='build', software='cp2k', sha='a' * 40, version='v1',
                        target='16v100-avx2', track='development', source_ref='master',
                        identity=identity, recipe_sha256=identity['recipe_sha256'],
                        controller=str(self.control), resume_run=None, dependencies_run='build-dependencies')
        self.assertFalse(self.probe(expected, 'software_controller.py', task))
        request = dict(expected, jobs=8, minutes=240, overlay_mb=32768, contract_schema=CONTRACT_SCHEMA)
        with patch.object(controller, 'ROOT', self.root), patch.object(controller, 'CONTROL', self.control):
            rendered = controller.render_job(argparse.Namespace(**request))
        (task / 'job.sbatch').write_text(rendered)
        (task / 'job.id').write_text('1002')
        (task / 'request.json').write_text(json.dumps(request))
        self.assertTrue(self.probe(expected, 'software_controller.py', task))
        request['dependencies_run'] = 'unrelated-dependency'
        (task / 'request.json').write_text(json.dumps(request))
        with self.assertRaises(subprocess.CalledProcessError):
            self.probe(expected, 'software_controller.py', task)
        request['dependencies_run'] = 'build-dependencies'
        (task / 'request.json').write_text(json.dumps(request))
        (task / 'job.sbatch').write_text(rendered + '# changed')
        with self.assertRaises(subprocess.CalledProcessError):
            self.probe(expected, 'software_controller.py', task)


class StatefulContinuationTests(unittest.TestCase):
    setUp = lifecycle.CiLifecycleTests.setUp
    execute = lifecycle.CiLifecycleTests.execute
    def test_acceptance_rerun_monitors_same_jobs_and_can_republish(self):
        for software in ('abacus', 'cp2k', 'gpumd'):
            state = self.root / software
            self.execute(software=software, target='16v100-avx2', phase='acceptance', persisted_jobs=state)
            recorded = {p.name for p in state.iterdir()}
            self.assertTrue(recorded)
            calls = self.execute(software=software, target='16v100-avx2', phase='acceptance', persisted_jobs=state)
            self.assertFalse(any(op in ('submit', 'prepare') for _, op in calls))
            self.assertEqual(calls[-1], ('software_controller.py', 'publish'))
            self.assertEqual(recorded, {p.name for p in state.iterdir()})

    def test_lost_monitor_and_application_rerun_reuse_existing_handle(self):
        state = self.root / 'application'
        self.execute(software='cp2k', phase='application', persisted_jobs=state,
                     fail_monitor='software_controller.py')
        self.assertEqual(len(list(state.iterdir())), 1)
        calls = self.execute(software='cp2k', phase='application', persisted_jobs=state)
        self.assertNotIn(('software_controller.py', 'submit'), calls)
        self.assertIn(('software_controller.py', 'monitor'), calls)

    def test_existing_passing_repeat_is_reused_after_original_regression(self):
        for software in ('abacus', 'cp2k', 'gpumd'):
            state = self.root / ('passed-repeat-' + software)
            self.execute(software=software, target='16v100-avx2', phase='acceptance', persisted_jobs=state,
                         publish_failures=1)
            recorded = {p.name for p in state.iterdir()}
            calls = self.execute(software=software, target='16v100-avx2', phase='acceptance', persisted_jobs=state,
                                 publish_failures=1)
            self.assertFalse(any(op in ('submit', 'prepare') for _, op in calls))
            self.assertEqual(calls[-1], ('software_controller.py', 'publish'))
            self.assertEqual(recorded, {p.name for p in state.iterdir()})

    def test_repeated_regression_cannot_allocate_third_run_after_job_retry(self):
        for software in ('abacus', 'cp2k', 'gpumd'):
            state = self.root / ('regression-' + software)
            self.execute(software=software, target='16v100-avx2', phase='acceptance', persisted_jobs=state,
                         publish_failures=2)
            recorded = {p.name for p in state.iterdir()}
            self.assertTrue(any(name.endswith('-retest') for name in recorded))
            calls = self.execute(software=software, target='16v100-avx2', phase='acceptance', persisted_jobs=state,
                                 publish_failures=2)
            self.assertFalse(any(op in ('submit', 'prepare') for _, op in calls))
            self.assertEqual(recorded, {p.name for p in state.iterdir()})
            self.assertEqual(calls.count(('software_controller.py', 'publish')), 2)


class RetestEvidenceStabilityTests(unittest.TestCase):
    def setUp(self):
        capture = redirect_stdout(io.StringIO())
        capture.__enter__()
        self.addCleanup(capture.__exit__, None, None, None)

    def test_abacus_remonitor_preserves_original_bytes_bound_by_retest(self):
        import test_abacus_benchmark as fixtures
        benchmark = fixtures.benchmark
        fixture = fixtures.BenchmarkTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        original, _, repeat, request, evidence = fixture.retest_pair()
        before = (original / 'results/evidence.json').read_bytes()
        receipt = benchmark.verify_retest(repeat, request, evidence)
        def scheduler(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0,
                '123|COMPLETED|0:0|\n' if argv[0] == 'sacct' else '', '')
        with patch.object(benchmark.subprocess, 'run', side_effect=scheduler):
            self.assertEqual(benchmark.monitor(argparse.Namespace(run_id='comparison', timeout=1, interval=0)), 0)
        self.assertEqual((original / 'results/evidence.json').read_bytes(), before)
        self.assertEqual(benchmark.verify_retest(repeat, request, evidence), receipt)

    def test_cp2k_remonitor_preserves_original_evidence_hash_pinned_by_retest(self):
        import test_cp2k_benchmark as fixtures
        benchmark = fixtures.benchmark
        fixture = fixtures.EvidenceTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.write('request.json', json.dumps(fixture.request))
        def scheduler(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0,
                '123|COMPLETED|0:0|\n' if argv[0] == 'sacct' else '', '')
        args = argparse.Namespace(run_id='probe', timeout=1, interval=0)
        with patch.object(benchmark.subprocess, 'run', side_effect=scheduler):
            self.assertEqual(benchmark.monitor(args), 0)
        before = (fixture.task / 'results/evidence.json').read_bytes()
        repeat = fixture.task.with_name('probe-retest')
        shutil.copytree(fixture.task, repeat)
        repeat_args = argparse.Namespace(run_id='probe-retest', target=fixture.request['target'],
                                         version='v1', build_run_id='build')
        benchmark.reference_for_submit(repeat_args, fixture.identity, fixture.request['artifact_sha256'])
        request = dict(fixture.request, **vars(repeat_args))
        (repeat / 'job.sbatch').write_text(benchmark.render_job(argparse.Namespace(**request)))
        request['job_script_sha256'] = checksum(repeat / 'job.sbatch')
        (repeat / 'request.json').write_text(json.dumps(request))
        evidence = benchmark.verify_evidence(repeat, request)
        receipt = benchmark.verify_retest(repeat, request, evidence)
        with patch.object(benchmark.subprocess, 'run', side_effect=scheduler):
            self.assertEqual(benchmark.monitor(args), 0)
        self.assertEqual((fixture.task / 'results/evidence.json').read_bytes(), before)
        self.assertEqual(benchmark.verify_retest(repeat, request, evidence), receipt)


    def test_gpumd_remonitor_preserves_raw_evidence_and_repeat_binding(self):
        import test_gpumd as fixtures
        acceptance, science = fixtures.acceptance, fixtures.science
        raw = fixtures.GpumdRawEvidenceTests()
        raw.setUp()
        self.addCleanup(raw.doCleanups)
        state = ContinuationPreflightTests()
        state.setUp()
        self.addCleanup(state.doCleanups)
        expected, request, _ = state.acceptance()
        (state.task / 'job.id').write_text('123')
        output = state.task / 'results/science'
        shutil.copytree(raw.root, output)
        gpu = state.task / 'results/gpu-host.txt'
        gpu.write_text('Tesla V100, GPU-original, 570\n')
        checks, timings = science.recheck_results(output, raw.report)
        report = dict(raw.report, identity=expected['identity'], gpu_metadata_sha256=checksum(gpu),
                      checks=checks, benchmark=timings, files=science.raw_file_inventory(output))
        (output / 'science.json').write_text(json.dumps(report))
        launcher = state.task / 'results/launcher'
        launcher.mkdir()
        shutil.copyfile(output / 'static-candidate/gold.xyz', launcher / 'dump.xyz')
        args = argparse.Namespace(run_id='build-science', timeout=1)
        with patch.object(acceptance, 'ROOT', state.root), patch.object(acceptance, 'CONTROL', state.control), \
                patch.object(acceptance.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout='')), \
                patch.object(acceptance.subprocess, 'check_output', return_value='123|COMPLETED|0:0|\n'):
            self.assertEqual(acceptance.monitor(args), 0)
            before = (state.task / 'results/evidence.json').read_bytes()
            first = acceptance.verify_evidence(state.task, request)
            binding = acceptance.repeat_binding(args.run_id, first)
            self.assertEqual(acceptance.monitor(args), 0)
            self.assertEqual((state.task / 'results/evidence.json').read_bytes(), before)
            self.assertEqual(acceptance.repeat_binding(args.run_id, first), binding)


if __name__ == '__main__':
    unittest.main()
