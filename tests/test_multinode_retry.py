"""A placement failure can retry once; scientific failures cannot."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'controller'))
import ci
from delivery_layout import CONTRACT_SCHEMA, artifact_path
from release_contract import make_identity
from source_cache import checksum
import test_ci_recovery as recovery
import software_controller


class MappingFailureEvidenceTests(unittest.TestCase):
    setUp = recovery.ContinuationPreflightTests.setUp

    def fixture(self):
        identity = make_identity('abacus', 'development', 'develop', 'a' * 40, 'v1',
                                 software_controller.recipe_fingerprint('abacus', self.control), 'dsprhbm')
        image = artifact_path(self.root, identity, 'build')
        image.parent.mkdir(parents=True)
        image.write_bytes(b'unchanged SIF')
        image.with_suffix('.json').write_text(json.dumps(dict(
            identity=identity, software='abacus', version='v1', target='dsprhbm',
            source_sha='a' * 40, recipe_sha256=identity['recipe_sha256'], artifact=str(image),
            sha256=checksum(image), build_verified=True, contract_schema=CONTRACT_SCHEMA)))
        self.task = self.root / 'runtime-tests/build-multinode'
        (self.task / 'results/ranks').mkdir(parents=True)
        (self.task / 'job.id').write_text('1001\n')
        (self.task / 'job.sbatch').write_text('#!/bin/bash\n# frozen invocation\n')
        expected = dict(run_id=self.task.name, build_run_id='build', version='v1', target='dsprhbm',
                        identity=identity, artifact=str(image))
        request = dict(expected, nodes=2, ranks_per_node=4, cpus_per_task=2, gpus_per_node=0,
                       artifact_sha256=checksum(image), launcher=str(self.control / 'abacus'),
                       launcher_sha256=checksum(self.control / 'abacus'),
                       controller_sha256=checksum(self.control / 'runtime_controller.py'),
                       job_script_sha256=checksum(self.task / 'job.sbatch'))
        (self.task / 'request.json').write_text(json.dumps(request))
        (self.task / 'results/status.json').write_text(json.dumps(
            dict(job='1001', state='FAILED', exit_code='254:0', verified=False)))
        self.log = self.task / 'results/abacus.log'
        self.log.write_text('Your job failed to map because the resulting process placement\n'
                            'would cause the process to be bound to CPUs in more than one\npackage:\n'
                            '  Mapping policy: BYNODE:NOOVERSUBSCRIBE\n  CPUs/rank: 2\n')
        self.accounting = self.root / 'accounting.txt'
        self.accounting.write_text('1001|FAILED|254:0|' + str(self.task) + '|\n')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        executable = self.bin / 'sacct'
        executable.write_text('#!/usr/bin/env python3\nfrom pathlib import Path\n'
                              'print(Path(' + repr(str(self.accounting)) + ').read_text(), end="")\n')
        executable.chmod(0o755)
        return expected, request, image

    def probe(self, expected):
        def ssh(argv, **kwargs):
            return subprocess.run(argv, check=True, text=True,
                                  env=dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH']),
                                  **kwargs)
        return ci.multinode_retry_allowed(ssh, str(self.root), str(self.control), str(self.task), expected)

    def test_real_remote_preflight_binds_failed_allocation_and_preserves_evidence(self):
        expected, request, image = self.fixture()
        before = {p: p.read_bytes() for p in (image, self.log, self.task / 'results/status.json')}
        self.assertTrue(self.probe(expected))
        receipt = self.task / 'results/mapping-retry.json'
        record = json.loads(receipt.read_text())
        self.assertEqual(record['retry_run'], 'build-multinode-retry1')
        self.assertEqual(record['artifact_sha256'], request['artifact_sha256'])
        self.assertEqual(record['log_sha256'], checksum(self.log))
        original = receipt.read_bytes()
        self.assertTrue(self.probe(expected))
        self.assertEqual(receipt.read_bytes(), original)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_changed_source_image_or_failure_log_cannot_authorize_retry(self):
        expected, _, image = self.fixture()
        self.assertTrue(self.probe(expected))
        for path in (image, self.control / 'runtime_controller.py', self.log):
            before = path.read_bytes()
            path.write_bytes(before + b'changed')
            with self.subTest(path=path), self.assertRaises(subprocess.CalledProcessError):
                self.probe(expected)
            path.write_bytes(before)

    def test_arbitrary_failure_and_started_calculation_never_retry(self):
        expected, _, _ = self.fixture()
        before = self.log.read_bytes()
        self.log.write_text('SCF failed to converge\n')
        self.assertFalse(self.probe(expected))
        self.log.write_bytes(before)
        for relative in ('results/ranks/rank-0.tsv', 'case/OUT.autotest/running_scf.log',
                         'results/final-energy-ev.txt'):
            path = self.task / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('calculation already started')
            with self.subTest(relative=relative):
                self.assertFalse(self.probe(expected))
            path.unlink()
            if relative.startswith('case/'):
                path.parent.rmdir()
        self.assertFalse((self.task / 'results/mapping-retry.json').exists())

    def test_live_wrong_allocation_or_other_failure_never_retry(self):
        expected, _, _ = self.fixture()
        for record in ('1001|RUNNING|0:0|' + str(self.task),
                       '1001|FAILED|254:0|/another/allocation',
                       '1001|FAILED|1:0|' + str(self.task), ''):
            self.accounting.write_text(record)
            with self.subTest(record=record):
                self.assertFalse(self.probe(expected))


class RetryLifecycleTests(unittest.TestCase):
    def exercise(self, *, eligible=True, retry_succeeds=True, preexisting=False):
        submitted, monitored, state = [], [], set()
        if preexisting:
            state.update(('build-multinode', 'build-multinode-retry1'))

        def python(script, operation, selected, *args):
            self.assertEqual(script, 'runtime_controller.py')
            if operation == 'submit':
                self.assertNotIn(selected, state)
                self.assertEqual(args, ('v1', 'dsprhbm', '--build-run-id', 'build'))
                state.add(selected)
                submitted.append(selected)
            else:
                monitored.append(selected)
                if selected == 'build-multinode' or not retry_succeeds:
                    raise subprocess.CalledProcessError(1, ['monitor', selected])

        if not eligible or not retry_succeeds:
            with self.assertRaises(subprocess.CalledProcessError):
                ci.accept_multinode(python, lambda _, run: run in state, lambda _: eligible,
                                    'build', 'v1', 'dsprhbm')
        else:
            ci.accept_multinode(python, lambda _, run: run in state, lambda _: eligible,
                                'build', 'v1', 'dsprhbm')
        return submitted, monitored

    def test_retry_keeps_same_artifact_and_never_exceeds_one_extra_allocation(self):
        runs = ['build-multinode', 'build-multinode-retry1']
        self.assertEqual(self.exercise(), (runs, runs))
        self.assertEqual(self.exercise(retry_succeeds=False), (runs, runs))

    def test_workflow_rerun_monitors_existing_retry_without_resubmitting(self):
        self.assertEqual(self.exercise(preexisting=True),
                         ([], ['build-multinode', 'build-multinode-retry1']))

    def test_scientific_failure_does_not_get_fresh_allocation(self):
        self.assertEqual(self.exercise(eligible=False), (['build-multinode'], ['build-multinode']))


if __name__ == '__main__':
    unittest.main()
