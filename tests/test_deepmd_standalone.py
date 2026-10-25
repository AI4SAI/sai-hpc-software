import copy
from contextlib import ExitStack, redirect_stdout
import io
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'controller'))
import deepmd_standalone as standalone
import deepmd_acceptance as acceptance
from deepmd_standalone_check import inventory, verify_science_files


class IndependentDeePMDTests(unittest.TestCase):
    def delivery(self, target='16v100-avx2'):
        return standalone.make_delivery(dict(track='development', source_ref='master',
            source_sha='5' * 40, source_version='master-2026-10-23'), target)

    def test_only_deepmd_identity_is_locked_and_recipe_drift_is_rejected(self):
        delivery = self.delivery()
        self.assertEqual(delivery['identity']['stack_sources'], {})
        self.assertEqual(standalone.validate_delivery(delivery), delivery)
        with patch.object(standalone, 'fingerprint', return_value='a' * 64):
            with self.assertRaisesRegex(ValueError, 'recipe'):
                standalone.validate_delivery(delivery)
        changed = copy.deepcopy(delivery)
        changed['lammps'] = {'source_sha': 'a' * 40}
        with self.assertRaisesRegex(ValueError, 'unexpected'):
            standalone.validate_delivery(changed)

    def test_every_target_has_project_billing_correct_qos_and_final_sif_science(self):
        for target in standalone.GPU_TARGETS:
            with self.subTest(target=target):
                script = standalone.render(self.delivery(target), 'independent-test')
                subprocess.run(['bash', '-n'], input=script, text=True, check=True)
                self.assertIn('#SBATCH --account=proj-sai-hpc-software\n', script)
                self.assertIn('#SBATCH --chdir=/org/org-open-sai/proj-sai-hpc-software/', script)
                self.assertIn('#SBATCH --qos=' + ('flood-1o2gpu' if target == '16v100-avx2' else 'rush-1o2gpu'), script)
                self.assertIn('#SBATCH --gpus-per-node=2', script)
                self.assertIn('BUILD_JOBS=12', script)
                self.assertIn('cp2k_image_driver.py', script)
                self.assertNotIn('afterok:', script)
                self.assertNotIn('/input/lammps', script)
                self.assertNotIn('TMPDIR=/tmp', script)
                self.assertNotIn('/home/stardust', script)
                final = [line for line in script.splitlines() if 'candidate.sif' in line and ' science ' in line]
                self.assertEqual(len(final), 1)
                self.assertNotIn('--fakeroot', final[0])
                self.assertNotIn('--overlay', final[0])
                self.assertIn('--nv', final[0])
                self.assertIn(str(standalone.task('independent-test') / 'runtime') + ':/tmp:rw', final[0])

    def test_missing_backend_or_gpu_evidence_cannot_be_scientific_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(FileNotFoundError):
                verify_science_files(temporary)

    def test_native_library_soname_symlinks_hash_the_actual_library(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary)
            site = prefix / 'lib/python3.13/site-packages/deepmd/lib'
            site.mkdir(parents=True)
            library = prefix / 'lib/libdeepmd_c.so.3'
            library.write_bytes(b'native-library')
            (prefix / 'lib/libdeepmd_c.so').symlink_to(library.name)
            for name in ('tf', 'pt', 'jax'):
                (site / ('libdeepmd_backend_' + name + '.so')).write_bytes(name.encode())
            with patch('deepmd_standalone_check.libraries', return_value='resolved dependencies'), \
                    patch('deepmd_standalone_check.importlib.metadata.version', return_value='3.2.0'):
                observed = inventory(prefix)
            self.assertEqual(observed['libraries'][str(prefix / 'lib/libdeepmd_c.so')]['resolved'], str(library))

    def test_fingerprint_covers_every_executed_source_and_detects_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            control = Path(temporary)
            for name in standalone.FILES:
                (control / name).write_bytes((ROOT / 'controller' / name).read_bytes())
            before = standalone.fingerprint(control)
            for name in ('md_science.py', 'md_environment.sh', 'deepmd_standalone_entry.sh'):
                path = control / name
                original = path.read_bytes()
                path.write_bytes(original + b'\n# changed\n')
                self.assertNotEqual(before, standalone.fingerprint(control))
                path.write_bytes(original)

    def test_acceptance_continuation_uses_original_sif_and_controller_with_project_tmp(self):
        source = dict(build_request=dict(delivery=self.delivery(),
                     controller=str(standalone.ROOT / 'controller/original/controller')),
                      artifact=str(standalone.task('original') / 'candidate.sif'))
        script = acceptance.render('science-continuation', source)
        subprocess.run(['bash', '-n'], input=script, text=True, check=True)
        self.assertIn(source['artifact'], script)
        self.assertIn(source['build_request']['controller'] + ':/control:ro', script)
        self.assertIn(':/acceptance:ro', script)
        self.assertIn('/runtime:/tmp:rw', script)
        self.assertIn('#SBATCH --account=proj-sai-hpc-software', script)
        self.assertNotIn('--overlay', script)
        self.assertNotIn('--fakeroot', script)
        self.assertNotIn('--bind /tmp:', script)

    def test_gpu_trace_and_baseline_use_explicit_backend_plugins(self):
        import deepmd_standalone_check as check
        prefix = '/opt/apps/deepmd-kit/independent/16V100'
        with tempfile.TemporaryDirectory() as temporary:
            def path(value):
                return Path(temporary) if str(value) == '/runtime' else Path(value)
            with patch.object(check, 'Path', side_effect=path), \
                    patch.object(check, 'require_execution_context'), \
                    patch.object(check, 'selected_identity', return_value={'install_prefix': prefix}), \
                    patch.object(check, 'verify_science_files', return_value={'passed': True}), \
                    patch.object(check.subprocess, 'run') as execute, \
                    patch.dict(check.os.environ, {'LD_LIBRARY_PATH': prefix + '/lib:/usr/lib', 'CUDA_VISIBLE_DEVICES': '0'}):
                check.science()
            evaluations = [call for call in execute.call_args_list
                           if call.args[0][0].endswith('/bin/python') and 'evaluate' in call.args[0]]
            self.assertEqual(len(evaluations), 12)
            for call in evaluations:
                installation = str(Path(call.args[0][0]).parent.parent)
                self.assertEqual(call.kwargs['env']['DP_BACKEND_PLUGIN_PATH'],
                                 installation + '/lib/python3.13/site-packages/deepmd/lib')
                self.assertEqual(call.kwargs['env']['CUDA_VISIBLE_DEVICES'], '0')
            profiles = [call for call in execute.call_args_list if call.args[0][1] == 'profile']
            self.assertEqual(len(profiles), 1)
            self.assertEqual(profiles[0].kwargs['env']['DP_BACKEND_PLUGIN_PATH'],
                             prefix + '/lib/python3.13/site-packages/deepmd/lib')


class DeePMDStatusTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(standalone, 'ROOT', self.root))
        self.stack.enter_context(patch.object(standalone, 'PROJECT', self.root))
        self.stack.enter_context(patch('remote_controller.SAI_PROJECT_ROOT', self.root))
        self.directory = self.root / 'runs/status-test'
        (self.directory / 'results').mkdir(parents=True)
        self.delivery = standalone.make_delivery(dict(track='development', source_ref='master',
            source_sha='5' * 40, source_version='master-2026-10-23'), '16v100-avx2')
        script = self.directory / 'job.sbatch'
        script.write_text(standalone.render(self.delivery, self.directory.name))
        request = dict(delivery=self.delivery, controller=str(standalone.CONTROL),
                       controller_sha256=standalone.fingerprint(),
                       job_script_sha256=standalone.checksum(script), job_id='123')
        (self.directory / 'request.json').write_text(json.dumps(request))
        self.science = self.stack.enter_context(patch('deepmd_standalone_check.verify_science_files',
                                                     return_value={'passed': True}))

    def row(self, state='COMPLETED', code='0:0', account='proj-sai-hpc-software', directory=None):
        return '|'.join(['123', state, code, '00:05:00', 'node', account, str(directory or self.directory)]) + '\n'

    def query(self, accounting):
        output = io.StringIO()
        with patch.object(standalone.subprocess, 'check_output', return_value=accounting), redirect_stdout(output):
            code = standalone.status(self.directory.name)
        record = json.loads(output.getvalue())
        self.assertEqual(record, json.loads((self.directory / 'results/status.json').read_text()))
        return code, record

    def build_files(self):
        image = self.directory / 'candidate.sif'
        if image.exists():
            image.chmod(0o644)
        image.write_bytes(b'candidate image')
        image.chmod(0o444)
        for name in ('build-receipt.json', 'science-receipt.json'):
            path = self.directory / 'results' / name
            if path.exists():
                path.unlink()
        with patch.dict(standalone.os.environ, {'SLURM_JOB_ID': '123'}):
            standalone.seal(self.directory.name, 'build')

    def successful_files(self, seal_science=True):
        self.build_files()
        runtime = self.directory / 'runtime'
        runtime.mkdir(exist_ok=True)
        for side in ('baseline', 'candidate'):
            for backend in ('tf', 'pt', 'jax'):
                for engine in ('python', 'c-api'):
                    (runtime / f'{side}-{backend}-{engine}.json').write_text(
                        json.dumps([{'slurm_job': '123'}] * 4))
        (runtime / 'pt-profiled.json').write_text(json.dumps([{'slurm_job': '123'}] * 4))
        for name in ('pt-cuda-kernels.csv', 'pt-cuda-trace.nsys-rep', 'runtime-env.sh',
                     'baseline-inventory.json', 'candidate-inventory.json', 'ddp-source-patch.json'):
            (runtime / name).write_text('fixed science evidence\n')
        (runtime / 'case').mkdir(exist_ok=True)
        (runtime / 'case/fixture.json').write_text('{"fixed": "input"}\n')
        (runtime / 'training').mkdir(exist_ok=True)
        (runtime / 'training/summary.json').write_text('{"job": "123"}\n')
        (runtime / 'science.json').write_text(json.dumps(self.science.return_value))
        if seal_science:
            with patch.dict(standalone.os.environ, {'SLURM_JOB_ID': '123'}):
                standalone.seal(self.directory.name, 'science')
        self.science.reset_mock()
        (self.directory / 'results/slurm-123.log').write_text(
            'DEEPMD_STANDALONE_BUILD_VERIFIED\n' +
            ('DEEPMD_STANDALONE_SCIENCE_VERIFIED_NOT_PUBLISHED\n' if seal_science else ''))

    def test_accounting_visibility_delay_is_pending_and_later_completion_still_verifies_science(self):
        self.successful_files()
        code, record = self.query('\n')
        self.assertEqual(code, 3)
        self.assertEqual(record['state'], 'PENDING_ACCOUNTING')
        self.assertFalse(record['build_verified'])
        self.assertFalse(record['scientific_verified'])
        self.science.assert_not_called()
        for account, directory in (('personal', self.directory), ('proj-sai-hpc-software', '/home/stardust')):
            with self.subTest(account=account, directory=directory), self.assertRaisesRegex(ValueError, 'account or directory'):
                self.query(self.row(account=account, directory=directory))
        self.science.assert_not_called()
        code, record = self.query(self.row())
        self.assertEqual(code, 0)
        self.assertTrue(record['scientific_verified'])
        self.science.assert_called_once_with(self.directory / 'runtime')

    def test_nonempty_invalid_accounting_and_command_failure_are_not_pending(self):
        for accounting in ('unexpected row\n', self.row() + self.row()):
            with self.subTest(accounting=accounting), self.assertRaisesRegex(ValueError, 'missing or ambiguous'):
                self.query(accounting)
        with patch.object(standalone.subprocess, 'check_output', side_effect=subprocess.CalledProcessError(1, ['sacct'])), \
                self.assertRaises(subprocess.CalledProcessError):
            standalone.status(self.directory.name)
        self.science.assert_not_called()

    def test_terminal_job_without_all_acceptance_gates_fails_instead_of_waiting(self):
        for marker in ('', 'DEEPMD_STANDALONE_BUILD_VERIFIED\n'):
            self.build_files()
            (self.directory / 'results/slurm-123.log').write_text(marker)
            with self.subTest(marker=marker):
                code, record = self.query(self.row())
                self.assertEqual(code, 1)
                self.assertFalse(record['scientific_verified'])
        self.science.assert_not_called()
        self.successful_files()
        for state, code, expected in (('RUNNING', '0:0', 3), ('PENDING', '0:0', 3),
                                      ('FAILED', '1:0', 1), ('COMPLETED', '1:0', 1)):
            with self.subTest(state=state, exit_code=code):
                result, record = self.query(self.row(state, code))
                self.assertEqual(result, expected)
                self.assertFalse(record['scientific_verified'])
        self.science.assert_not_called()
        self.science.side_effect = ValueError('raw science evidence failed')
        with self.assertRaisesRegex(ValueError, 'raw science evidence'):
            self.query(self.row())


    def test_changed_sif_and_replaced_status_cannot_update_the_fixed_build_hash(self):
        self.successful_files()
        self.assertEqual(self.query(self.row())[0], 0)
        image = self.directory / 'candidate.sif'
        image.chmod(0o644)
        image.write_bytes(b'replaced candidate')
        image.chmod(0o444)
        (self.directory / 'results/status.json').write_text(json.dumps(dict(
            scientific_verified=True, artifact_sha256=hashlib.sha256(image.read_bytes()).hexdigest())))
        with self.assertRaisesRegex(ValueError, 'fixed build receipt'):
            self.query(self.row())

    def test_writable_artifact_and_missing_receipt_fail_closed(self):
        self.successful_files()
        image = self.directory / 'candidate.sif'
        image.chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'immutable file'):
            self.query(self.row())
        image.chmod(0o444)
        (self.directory / 'results/build-receipt.json').unlink()
        with self.assertRaisesRegex(ValueError, 'immutable file'):
            self.query(self.row())

    def test_completion_receipts_cannot_be_overwritten_or_written_by_another_job(self):
        self.successful_files()
        for phase in ('build', 'science'):
            path = self.directory / 'results' / (phase + '-receipt.json')
            before = path.read_bytes()
            with patch.dict(standalone.os.environ, {'SLURM_JOB_ID': '123'}):
                with self.assertRaises(FileExistsError):
                    standalone.seal(self.directory.name, phase)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(path.stat().st_mode & 0o777, 0o444)
            with patch.dict(standalone.os.environ, {'SLURM_JOB_ID': '456'}):
                with self.assertRaisesRegex(ValueError, 'inside its build allocation'):
                    standalone.seal(self.directory.name, phase)

    def test_science_remains_bound_to_the_original_sif_when_build_receipt_is_replaced(self):
        self.successful_files()
        image = self.directory / 'candidate.sif'
        image.chmod(0o644)
        image.write_bytes(b'another candidate with unrelated science')
        image.chmod(0o444)
        path = self.directory / 'results/build-receipt.json'
        path.chmod(0o644)
        path.write_text(json.dumps(standalone.build_receipt(self.directory,
                                    standalone.read_request(self.directory))))
        path.chmod(0o444)
        with self.assertRaisesRegex(ValueError, 'fixed SIF/evidence receipt'):
            self.query(self.row())

    def test_mutated_science_and_foreign_allocation_cannot_reuse_acceptance(self):
        self.successful_files()
        (self.directory / 'runtime/pt-cuda-trace.nsys-rep').write_bytes(b'another trace')
        with self.assertRaisesRegex(ValueError, 'fixed SIF/evidence receipt'):
            self.query(self.row())
        path = self.directory / 'runtime/candidate-pt-c-api.json'
        path.write_text(json.dumps([{'slurm_job': '456'}] * 4))
        with self.assertRaisesRegex(ValueError, 'different allocation'):
            self.query(self.row())

    def test_training_checkpoint_link_is_bound_without_following_external_links(self):
        from deepmd_standalone_check import science_evidence_hashes
        self.successful_files(seal_science=False)
        runtime = self.directory / 'runtime'
        case = runtime / 'training/se_e2_a'
        case.mkdir()
        checkpoint = case / 'model.ckpt-12.pt'
        checkpoint.write_bytes(b'weights')
        alias = case / 'model.ckpt.pt'
        alias.symlink_to(checkpoint.name)
        first = science_evidence_hashes(runtime, '123')['training']
        checkpoint.write_bytes(b'changed weights')
        self.assertNotEqual(first, science_evidence_hashes(runtime, '123')['training'])
        alias.unlink()
        alias.symlink_to('../../science.json')
        with self.assertRaisesRegex(ValueError, 'redirected DeePMD training'):
            science_evidence_hashes(runtime, '123')

    def test_missing_or_redirected_inputs_cannot_be_sealed_for_the_first_time(self):
        self.successful_files(seal_science=False)
        receipt = self.directory / 'results/science-receipt.json'
        for name in ('case', 'runtime-env.sh', 'baseline-inventory.json', 'candidate-inventory.json'):
            path = self.directory / 'runtime' / name
            saved = path.with_name(path.name + '.saved')
            path.rename(saved)
            try:
                for redirected in (False, True):
                    if redirected:
                        path.symlink_to(saved)
                    with self.subTest(name=name, redirected=redirected), \
                            patch.dict(standalone.os.environ, {'SLURM_JOB_ID': '123'}):
                        with self.assertRaisesRegex(ValueError, 'missing, redirected or invalid'):
                            standalone.seal(self.directory.name, 'science')
                    self.assertFalse(receipt.exists())
            finally:
                if path.is_symlink():
                    path.unlink()
                saved.rename(path)


class DeePMDAcceptanceStatusTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(standalone, 'ROOT', self.root))
        self.stack.enter_context(patch.object(acceptance, 'ROOT', self.root))
        self.stack.enter_context(patch.object(acceptance, 'PROJECT', self.root))
        self.stack.enter_context(patch('remote_controller.SAI_PROJECT_ROOT', self.root))
        self.directory = self.root / 'runs/acceptance-status'
        (self.directory / 'results').mkdir(parents=True)
        delivery = standalone.make_delivery(dict(track='development', source_ref='master',
            source_sha='5' * 40, source_version='master-2026-10-23'), '16v100-avx2')
        source = dict(source_run='original', build_request=dict(delivery=delivery,
            controller=str(self.root / 'controller/original')), artifact=str(self.root / 'runs/original/candidate.sif'))
        self.original_origin = acceptance.origin
        self.stack.enter_context(patch.object(acceptance, 'origin', return_value=source))
        script = self.directory / 'job.sbatch'
        script.write_text(acceptance.render(self.directory.name, source))
        request = dict(source=source, controller=str(acceptance.CONTROL),
            controller_sha256=acceptance.acceptance_fingerprint(), job_script_sha256=standalone.checksum(script), job_id='456')
        (self.directory / 'request.json').write_text(json.dumps(request))
        self.science = self.stack.enter_context(patch('deepmd_standalone_check.verify_science_files',
            return_value={'passed': True}))

    def row(self, state='COMPLETED', code='0:0', account='proj-sai-hpc-software', directory=None):
        return '|'.join(['456', state, code, '00:00:30', 'node', account, str(directory or self.directory)]) + '\n'

    def query(self, accounting):
        output = io.StringIO()
        with patch.object(acceptance.subprocess, 'check_output', return_value=accounting), redirect_stdout(output):
            code = acceptance.status(self.directory.name)
        record = json.loads(output.getvalue())
        self.assertEqual(record, json.loads((self.directory / 'results/status.json').read_text()))
        return code, record

    def test_delayed_accounting_and_all_active_states_remain_pending_until_verified_completion(self):
        code, result = self.query('\n')
        self.assertEqual(code, 3)
        self.assertEqual(result['state'], 'PENDING_ACCOUNTING')
        self.assertFalse(result['scientific_verified'])
        for state in ('PENDING', 'RUNNING', 'CONFIGURING', 'COMPLETING', 'SUSPENDED',
                      'REQUEUED', 'REQUEUE_FED', 'REQUEUE_HOLD', 'RESIZING', 'SIGNALING', 'STAGE_OUT', 'RUNNING+'):
            with self.subTest(state=state):
                code, result = self.query(self.row(state))
                self.assertEqual(code, 3)
                self.assertFalse(result['scientific_verified'])
        self.science.assert_not_called()
        (self.directory / 'results/slurm-456.log').write_text(
            'DEEPMD_INDEPENDENT_ACCEPTANCE_VERIFIED_NOT_PUBLISHED\n')
        code, result = self.query(self.row())
        self.assertEqual(code, 0)
        self.assertTrue(result['scientific_verified'])
        self.science.assert_called_once_with(self.directory / 'runtime')

    def test_terminal_missing_marker_and_failed_jobs_do_not_wait_or_claim_science(self):
        for state, code in (('COMPLETED', '0:0'), ('COMPLETED', '1:0'), ('FAILED', '1:0'), ('CANCELLED by 123', '0:0')):
            with self.subTest(state=state, code=code):
                outcome, result = self.query(self.row(state, code))
                self.assertEqual(outcome, 1)
                self.assertFalse(result['scientific_verified'])
        self.science.assert_not_called()

    def test_nonempty_invalid_or_wrong_project_accounting_is_not_a_visibility_delay(self):
        for accounting in ('unexpected row\n', self.row() + self.row()):
            with self.subTest(accounting=accounting), self.assertRaisesRegex(ValueError, 'missing or ambiguous'):
                self.query(accounting)
        for accounting in (self.row(account='personal'), self.row(directory='/home/stardust')):
            with self.subTest(accounting=accounting), self.assertRaisesRegex(ValueError, 'project account/work directory'):
                self.query(accounting)
        with patch.object(acceptance.subprocess, 'check_output', side_effect=subprocess.CalledProcessError(1, ['sacct'])):
            with self.assertRaises(subprocess.CalledProcessError):
                acceptance.status(self.directory.name)
        self.science.assert_not_called()

    def test_cancelled_by_user_can_continue_an_immutable_verified_image(self):
        directory = self.root / 'runs/cancelled-original'
        (directory / 'results').mkdir(parents=True)
        control = self.root / 'controller/frozen'
        control.mkdir(parents=True)
        (directory / 'request.json').write_text(json.dumps(dict(controller=str(control), job_id='789')))
        (directory / 'results/slurm-789.log').write_text('DEEPMD_STANDALONE_BUILD_VERIFIED\n')
        image = directory / 'candidate.sif'
        image.write_bytes(b'verified original candidate')
        image.chmod(0o444)
        with patch.object(acceptance.subprocess, 'run'):
            for state in ('CANCELLED', 'CANCELLED by 1234', 'CANCELLED+'):
                row = '|'.join(['789', state, '0:0', '00:04:00', 'node', 'proj-sai-hpc-software', str(directory)])
                with self.subTest(state=state), patch.object(acceptance.subprocess, 'check_output', return_value=row):
                    result = self.original_origin(directory.name)
                    self.assertEqual(result['build_accounting'], row)
                    self.assertEqual(result['artifact_sha256'], standalone.checksum(image))


if __name__ == '__main__':
    unittest.main()
