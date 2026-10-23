"""Standalone GPU acceptance preserves the previously accepted numerical matrix."""
import ast
from contextlib import ExitStack, redirect_stdout
import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'controller'))
import lammps_acceptance as acceptance
import md_lammps_standalone as standalone
from release_contract import claim_build
from remote_controller import SAI_PROJECT_ACCOUNT, SAI_PROJECT_ROOT


class ScientificContractTests(unittest.TestCase):
    def test_numerical_parser_matrix_and_comparisons_are_the_accepted_implementation(self):
        original = (REPO / 'docs/evidence/lammps-independent-20261022/acceptance.py').read_text()
        original = original.replace('control/rank.sh', 'control/lammps_rank.sh')
        current = Path(acceptance.__file__).read_text()
        def functions(text):
            return {node.name: ast.dump(node, include_attributes=False) for node in ast.parse(text).body
                    if isinstance(node, ast.FunctionDef)}
        before, after = functions(original), functions(current)
        for name in ('inventory', 'expected_cases', 'compare_science', 'help_version',
                     'verify_raw', 'raw_inventory', 'command', 'execute', 'run'):
            with self.subTest(function=name):
                self.assertEqual(after[name], before[name])
        reference = REPO / 'deployment/lammps-30Sep2026/native-reference.json'
        self.assertEqual(acceptance.digest(reference), acceptance.REFERENCE_SHA256)
        for partition, record in acceptance.read(reference)['partitions'].items():
            with self.subTest(partition=partition):
                self.assertEqual(len(acceptance.expected_cases(record) - {'version', 'inventory'}), 14)

    def test_three_partitions_bill_project_and_use_private_short_scratch(self):
        with patch.object(acceptance, 'PROJECT', SAI_PROJECT_ROOT), \
                patch.object(acceptance, 'ROOT', SAI_PROJECT_ROOT / 'experimental/deepmd-lammps'):
            for partition in ('4V100', '16V100', '8V100V0'):
                output = acceptance.ROOT / 'runtime-tests' / ('science-' + partition)
                script = acceptance.render(output, partition)
                subprocess.run(['bash', '-n'], input=script, text=True, check=True)
                self.assertIn('#SBATCH --account=' + SAI_PROJECT_ACCOUNT, script)
                self.assertIn('#SBATCH --chdir=' + str(output), script)
                self.assertIn('#SBATCH --export=NIL', script)
                self.assertIn('#SBATCH --qos=' + ('flood-1o2gpu' if partition == '16V100' else 'rush-1o2gpu'), script)
                self.assertIn('export TMPDIR=' + str(acceptance.runtime_path(output)), script)
                self.assertTrue(acceptance.runtime_path(output).is_relative_to(SAI_PROJECT_ROOT / 'runtime'))
                self.assertNotIn('export TMPDIR=/tmp', script)
                self.assertNotIn('CUDA_VISIBLE_DEVICES=', script)
        subprocess.run(['bash', '-n', str(REPO / 'controller/lammps_rank.sh')], check=True)

    def test_reference_inputs_cannot_escape_the_frozen_source_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'file').write_text('reference')
            (root / 'redirect').symlink_to('file')
            for relative in ('../outside', '/etc/passwd', 'redirect'):
                with self.subTest(relative=relative), self.assertRaises(ValueError):
                    acceptance.reference_path(root, relative)
            self.assertEqual(acceptance.reference_path(root, 'file'), root / 'file')


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.project = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.root = self.project / 'experimental/deepmd-lammps'
        self.build = self.root / 'runs/build'
        self.build.mkdir(parents=True)
        (self.build / 'request.json').write_text('frozen build request')
        self.delivery = standalone.make_delivery(dict(track='development', source_ref='develop',
            source_sha='a' * 40, source_version='develop'), '16v100-avx2')
        self.status = dict(delivery=self.delivery, artifact=str(self.build / 'candidate.sif'),
                           artifact_sha256='c' * 64, build_verified=True)
        self.stack.enter_context(patch.object(acceptance, 'PROJECT', self.project))
        self.stack.enter_context(patch.object(acceptance, 'ROOT', self.root))
        self.build_check = self.stack.enter_context(patch.object(acceptance, 'build_status', return_value=self.status))
        self.legacy = self.project / 'legacy-readonly'
        self.legacy.mkdir()
        (self.legacy / 'fixture.txt').write_text('accepted frozen input')
        self.reference = self.project / 'native-reference.json'
        self.reference.write_text(json.dumps(dict(partitions={'16V100': dict(tools={},
            science_case='.', case_files={'fixture.txt': acceptance.digest(self.legacy / 'fixture.txt')}, gpu={})})))
        self.stack.enter_context(patch.object(acceptance, 'REFERENCE_SHA256', acceptance.digest(self.reference)))

    def test_submit_freezes_before_releasing_held_job_and_never_resubmits(self):
        output = self.root / 'runtime-tests/build-science'
        commands = []
        def scheduler(argv, **kwargs):
            commands.append(argv)
            if argv[0] == 'sbatch':
                self.assertEqual(kwargs['cwd'], output)
                request, _, _, _ = acceptance.context(output)
                self.assertEqual(request['expected_delivery'], self.delivery)
                self.assertEqual((output / 'inputs/science/fixture.txt').read_text(), 'accepted frozen input')
            if argv[:2] == ['scontrol', 'release']:
                self.assertEqual((output / 'job.id').read_text(), '123\n')
            return subprocess.CompletedProcess(argv, 0)
        def allocate(argv, **kwargs):
            self.assertIn('--hold', argv)
            self.assertEqual(kwargs['cwd'], output)
            acceptance.context(output)
            return '123;cluster\n'
        with patch.object(acceptance.subprocess, 'run', side_effect=scheduler), \
                patch.object(acceptance.subprocess, 'check_output', side_effect=allocate), redirect_stdout(io.StringIO()):
            self.assertEqual(acceptance.submit(self.build, self.reference, expected_delivery=self.delivery,
                                              reference_root=self.legacy), output)
            with self.assertRaisesRegex(ValueError, 'already exists'):
                acceptance.submit(self.build, self.reference, expected_delivery=self.delivery,
                                  reference_root=self.legacy)
        self.assertEqual(commands[-1], ['scontrol', 'release', '123'])
        self.assertEqual(stat.S_IMODE(acceptance.runtime_path(output).stat().st_mode), 0o700)
        self.assertEqual((self.build / 'request.json').read_text(), 'frozen build request')
        (output / 'inputs/science/fixture.txt').chmod(0o600)
        (output / 'inputs/science/fixture.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'pinned file changed'):
            acceptance.context(output)

    def test_wrong_source_or_reference_is_rejected_before_allocating(self):
        changed = copy.deepcopy(self.delivery)
        changed['identity']['source_sha'] = 'd' * 40
        with patch.object(acceptance.subprocess, 'run') as scheduler:
            with self.assertRaisesRegex(ValueError, 'source/dependency'):
                acceptance.submit(self.build, self.reference, expected_delivery=changed, reference_root=self.legacy)
            self.reference.write_text('{}')
            with self.assertRaisesRegex(ValueError, 'accepted reference'):
                acceptance.submit(self.build, self.reference, expected_delivery=self.delivery, reference_root=self.legacy)
            scheduler.assert_not_called()
        self.assertFalse((self.root / 'runtime-tests').exists())

    def test_monitor_never_accepts_failed_job_or_missing_evidence(self):
        output = self.root / 'runtime-tests/science'
        output.mkdir(parents=True)
        (output / 'job.id').write_text('123\n')
        context = ({}, {}, self.delivery['identity'], {})
        with patch.object(acceptance, 'context', return_value=context):
            for state, code in (('FAILED', '1:0'), ('COMPLETED', '1:0'), ('COMPLETED', '0:0')):
                row = ['123', state, code, '16V100', 'test', str(output)]
                with self.subTest(state=state, code=code), patch.object(acceptance, 'accounting', return_value=row), \
                        redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
                    acceptance.monitor(output, timeout=1, interval=0)
            (output / 'acceptance.json').write_text('{}')
            row = ['123', 'COMPLETED', '0:0', '16V100', 'test', str(output)]
            with patch.object(acceptance, 'accounting', return_value=row), redirect_stdout(io.StringIO()):
                self.assertEqual(acceptance.monitor(output, timeout=1, interval=0), 0)
            self.assertFalse((output / 'verification.json').exists())


class BuildProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.project = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.root = self.project / 'experimental/deepmd-lammps'
        self.build = self.root / 'runs/build'
        (self.build / 'results').mkdir(parents=True)
        control = self.root / 'controller/frozen'
        control.mkdir(parents=True)
        # A small frozen controller exercises the real isolated interpreter;
        # dependency byte/link verification itself is covered by standalone tests.
        (control / 'md_lammps_standalone.py').write_text('''
import hashlib, json
from pathlib import Path
ROOT = Path(__file__).parent
def validate_delivery(delivery):
    if delivery != json.loads((ROOT / 'delivery.json').read_text()):
        raise ValueError('original delivery changed')
    return delivery
def dependency(target):
    if target != '16v100-avx2':
        raise ValueError('wrong dependency target')
    return ROOT / 'dependency', '@DEPENDENCY_SHA256@'
def verify_dependency(path, checksum):
    if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
        raise ValueError('original dependency changed')
'''.replace('@DEPENDENCY_SHA256@', hashlib.sha256(b'accepted dependency').hexdigest()))
        self.dependency = control / 'dependency'
        self.dependency.write_bytes(b'accepted dependency')
        self.delivery = dict(identity=dict(target='16v100-avx2', partition='16V100'))
        (control / 'delivery.json').write_text(json.dumps(self.delivery))
        (self.build / 'candidate.sif').write_bytes(b'verified SIF')
        (self.build / 'job.id').write_text('123\n')
        (self.build / 'job.sbatch').write_text('#!/bin/bash\n')
        (self.build / 'results/slurm-123.log').write_text('LAMMPS_STANDALONE_BUILD_VERIFIED_NOT_PUBLISHED\n')
        self.request = dict(controller=str(control), delivery=self.delivery,
                            job_script_sha256=acceptance.digest(self.build / 'job.sbatch'))
        (self.build / 'request.json').write_text(json.dumps(self.request))
        self.status = dict(job='123', delivery=self.delivery, build_verified=True,
            state='COMPLETED', exit_code='0:0', artifact=str(self.build / 'candidate.sif'),
            artifact_sha256=acceptance.digest(self.build / 'candidate.sif'))
        (self.build / 'results/status.json').write_text(json.dumps(self.status))
        for name, value in (('PROJECT', self.project), ('ROOT', self.root), ('SAI_PROJECT_ROOT', self.project)):
            self.stack.enter_context(patch.object(acceptance, name, value))
        self.row = ['123', 'COMPLETED', '0:0', '16V100', SAI_PROJECT_ACCOUNT, str(self.build)]
        self.stack.enter_context(patch.object(acceptance, 'accounting', return_value=self.row))

    def test_original_controller_and_completed_project_allocation_are_required(self):
        self.assertEqual(acceptance.build_status(self.build), self.status)
        self.row[4] = 'personal-account'
        with self.assertRaisesRegex(ValueError, 'project account'):
            acceptance.build_status(self.build)
        self.row[4] = SAI_PROJECT_ACCOUNT
        self.row[5] = '/home/stardust'
        with self.assertRaisesRegex(ValueError, 'working directory'):
            acceptance.build_status(self.build)
        self.row[5] = str(self.build)
        self.dependency.write_bytes(b'changed dependency')
        with self.assertRaises(subprocess.CalledProcessError):
            acceptance.build_status(self.build)
        self.assertFalse(any(self.root.rglob('__pycache__')))

    def test_changed_image_job_or_redirected_metadata_never_pass(self):
        image = self.build / 'candidate.sif'
        image.write_bytes(b'changed SIF')
        with self.assertRaisesRegex(ValueError, 'image changed'):
            acceptance.build_status(self.build)
        image.write_bytes(b'verified SIF')
        (self.build / 'job.sbatch').write_text('# changed script\n')
        with self.assertRaisesRegex(ValueError, 'provenance changed'):
            acceptance.build_status(self.build)
        (self.build / 'job.sbatch').write_text('#!/bin/bash\n')
        status = self.build / 'results/status.json'
        status.rename(self.build / 'other-status.json')
        status.symlink_to('../other-status.json')
        with self.assertRaisesRegex(ValueError, 'untrusted build metadata'):
            acceptance.build_status(self.build)


class ClaimedBuildTests(unittest.TestCase):
    def test_only_matching_attempt_receipt_may_precede_a_build(self):
        delivery = standalone.make_delivery(dict(track='release', source_ref='stable_test',
            source_sha='a' * 40, source_version='stable_test'), '16v100-avx2')
        for fault in (None, 'request', 'overlay', 'wrong-source', 'skipped', 'symlink'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                claim_build(root, [delivery['identity']], 'build')
                task = root / 'runs/build'
                receipt = task / 'input/build-attempt.json'
                if fault == 'request':
                    (task / 'request.json').write_text('{}')
                elif fault == 'overlay':
                    (task / 'work.ext3').write_bytes(b'old overlay')
                elif fault in ('wrong-source', 'skipped'):
                    record = acceptance.read(receipt)
                    if fault == 'wrong-source':
                        record['identities'][0]['source_sha'] = 'b' * 40
                    else:
                        record.update(build=False, identities=[], skipped=[{'previous_run': 'old'}])
                    receipt.write_text(json.dumps(record))
                elif fault == 'symlink':
                    other = root / 'other.json'
                    receipt.replace(other)
                    receipt.symlink_to(other)
                if fault is None:
                    standalone.validate_claim_directory(task, 'build', delivery)
                else:
                    with self.assertRaises(ValueError):
                        standalone.validate_claim_directory(task, 'build', delivery)


if __name__ == '__main__':
    unittest.main()
