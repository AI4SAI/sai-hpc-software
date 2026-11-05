import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import managed_overlay as managed
from remote_controller import container_command


class ManagedOverlayTests(unittest.TestCase):
    def test_parser_preserves_payload_arguments_and_ignores_payload_overlay_flag(self):
        command = container_command('/images/base.sif', ['program', '--overlay', '/not-an-image'],
                                    overlay='/project/runs/build/work.ext3', gpu=True)[1:]
        result = managed.inspect_command(command, '/project/runs/build')
        self.assertEqual(command[result['index']], '/project/runs/build/work.ext3')
        self.assertEqual(result['image'], '/images/base.sif')
        command[result['index']] += ':ro'
        readonly = managed.inspect_command(command, '/project/runs/build')
        self.assertTrue(readonly['readonly'])
        self.assertEqual(readonly['overlay'], result['overlay'])
        self.assertFalse(result['readonly'])

    def test_wrong_image_multiple_writers_and_unknown_policy_are_rejected(self):
        for command in (
            ['exec', '--overlay', '/elsewhere/work.ext3', '/base.sif', 'true'],
            ['exec', '--overlay', '/elsewhere/work.ext3:ro', '/base.sif', 'true'],
            ['exec', '--overlay', '/project/runs/build/work.ext3', '--overlay', '/another:ro', '/base.sif', 'true'],
            ['exec', '--overlay', '/project/runs/build/work.ext3', '--overlay', '/dependency/work.ext3', '/base.sif', 'true'],
            ['exec', '--overlay', '/project/runs/build/work.ext3', '--overlay', '/project/runs/build/work.ext3:ro', '/base.sif', 'true'],
            ['exec', '--overlay', '/project/runs/build/work.ext3', '--overlay', '../dependency/work.ext3:ro', '/base.sif', 'true'],
            ['exec', '--writable', '/base.sif', 'true'],
        ):
            with self.assertRaises(ValueError):
                managed.inspect_command(command, '/project/runs/build')

    def test_readonly_dependency_preserves_the_managed_writer(self):
        command = ['exec', '--overlay', '/dependency/work.ext3:ro',
                   '--overlay', '/project/runs/build/work.ext3', '/base.sif', 'true']
        policy = managed.inspect_command(command, '/project/runs/build')
        self.assertEqual(command[policy['index']], '/project/runs/build/work.ext3')
        self.assertEqual(policy['dependency_overlays'], ['/dependency/work.ext3'])
        self.assertFalse(policy['readonly'])
        command[1:1] = ['--overlay', '/dependency/work.ext3:ro']
        with self.assertRaisesRegex(ValueError, 'distinct read-only'):
            managed.inspect_command(command, '/project/runs/build')

    def test_nested_environment_removes_outer_binds_but_preserves_explicit_payload_variables(self):
        env = dict(APPTAINER_BIND='/outer', APPTAINER_CONTAINER='/base.sif',
                   SINGULARITY_BINDPATH='/outer', APPTAINERENV_SENTINEL='value',
                   APPTAINER_TMPDIR='/project/tmp', APPTAINER_CACHEDIR='/project/cache',
                   LD_PRELOAD='/fakeroot.so', SLURM_JOB_ID='123')
        self.assertEqual(managed.clean_environment(env), dict(
            APPTAINERENV_SENTINEL='value', APPTAINER_TMPDIR='/project/tmp',
            APPTAINER_CACHEDIR='/project/cache', SLURM_JOB_ID='123'))

    def test_sif_execution_keeps_original_command_and_binary_stdout_route(self):
        argv = ['exec', '/base.sif', 'cat', '/payload']
        execute = Mock(return_value=17)
        result = managed.launch('/project', '/project/runs/test', '/app', '/config', argv, execute)
        self.assertEqual(result, 17)
        execute.assert_called_once_with(['/app', '--config', '/config', *argv], '/project/runs/test')

    def test_launch_records_original_policy_and_uses_same_runtime_and_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root/'runs/test'
            (run/'runtime').mkdir(parents=True)
            (run/'results').mkdir()
            (run/'work.ext3').write_bytes(b'image')
            dependency = root/'dependency/work.ext3'
            dependency.parent.mkdir()
            dependency.write_bytes(b'accepted read-only build dependency')
            argv = container_command('/base.sif', ['true'], overlay=str(run/'work.ext3'), gpu=True)[1:]
            argv[1:1] = ['--bind', str(run/'results')+':/results:rw',
                         '--overlay', str(dependency)+':ro']
            original = argv.copy()
            captured = {}
            def execute(command, target):
                captured.update(command=command, target=target, env=dict(os.environ))
                request = json.loads(Path(command[-1]).read_text())
                receipt = run/'results'/('managed-overlay-'+request['token']+'.json')
                receipt.write_text(json.dumps(dict(completed=True, accepted=True, forced_stop=False,
                    payload_returncode=0, driver_returncode=0, unmount_returncode=0,
                    overlay=str(run/'work.ext3'), apptainer='/stock/apptainer',
                    readonly=request['policy']['readonly'])))
                return 0
            with patch.dict(os.environ, APPTAINERENV_SENTINEL='value'):
                before = dict(os.environ)
                self.assertEqual(managed.launch(root, run, '/stock/apptainer', '/config', argv, execute), 0)
                self.assertEqual(dict(os.environ), before)
            self.assertEqual(argv, original)
            self.assertEqual(captured['command'][0], '/stock/apptainer')
            self.assertIn('/base.sif', captured['command'])
            self.assertIn(str(dependency)+':'+str(dependency)+':ro', captured['command'])
            if Path('/etc/ld.so.cache').exists():
                self.assertIn('/etc/ld.so.cache:/etc/ld.so.cache:ro', captured['command'])
            # Driver discovery belongs to the inner --nv; the outer layer
            # exposes the host cache without injecting its own GPU libraries.
            self.assertNotIn('--nv', captured['command'])
            request = json.loads(Path(captured['command'][-1]).read_text())
            self.assertEqual(request['argv'], original)
            self.assertEqual(request['forwarded_env']['APPTAINERENV_SENTINEL'], 'value')
            self.assertTrue(Path(captured['env']['TMPDIR']).is_relative_to(run/'runtime'))
            # A zero from the outer runtime without a driver's final receipt
            # must never authorize consuming the writable image.
            self.assertEqual(managed.launch(root, run, '/stock/apptainer', '/config', argv,
                                            lambda command, target: 0), 74)

    def test_readonly_export_requires_managed_receipt_with_readonly_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root/'runs/export'
            (run/'runtime').mkdir(parents=True)
            (run/'results').mkdir()
            (run/'work.ext3').write_bytes(b'completed image')
            argv = ['exec', '--overlay', str(run/'work.ext3')+':ro', '/base.sif', 'cat', '/payload']
            modes = iter((True, False))
            def execute(command, target):
                request = json.loads(Path(command[-1]).read_text())
                self.assertTrue(request['policy']['readonly'])
                self.assertEqual(request['argv'], argv)
                receipt = run/'results'/('managed-overlay-'+request['token']+'.json')
                receipt.write_text(json.dumps(dict(completed=True, accepted=True, forced_stop=False,
                    payload_returncode=0, driver_returncode=0, unmount_returncode=0,
                    overlay=str(run/'work.ext3'), apptainer='/app', readonly=next(modes))))
                return 0
            self.assertEqual(managed.launch(root, run, '/app', '/config', argv, execute), 0)
            self.assertEqual(managed.launch(root, run, '/app', '/config', argv, execute), 74)

    def test_teardown_waits_for_natural_exit_and_preserves_failure(self):
        process = Mock()
        process.wait.return_value = 0
        process.poll.return_value = 0
        with patch.object(managed, 'mounted', return_value=True), \
                patch.object(managed.subprocess, 'run', return_value=Mock(returncode=0, stderr='')):
            record = {}
            self.assertTrue(managed.stop_driver(process, Path('/mount'), record))
            process.wait.assert_called_once_with(timeout=managed.EXIT_TIMEOUT)
            process.terminate.assert_not_called()
            process.wait.return_value = 5
            self.assertFalse(managed.stop_driver(process, Path('/mount'), {}))

    def test_timeout_or_failed_unmount_cannot_be_accepted(self):
        process = Mock()
        process.wait.side_effect = [subprocess.TimeoutExpired(['fuse2fs'], managed.EXIT_TIMEOUT), 0]
        process.poll.return_value = None
        with patch.object(managed, 'mounted', return_value=True), \
                patch.object(managed.subprocess, 'run', return_value=Mock(returncode=0, stderr='')):
            record = {}
            self.assertFalse(managed.stop_driver(process, Path('/mount'), record))
            self.assertTrue(record['forced_stop'])
            self.assertTrue(record['driver_timeout'])
        process = Mock()
        process.wait.return_value = 0
        process.poll.return_value = 0
        with patch.object(managed, 'mounted', return_value=True), \
                patch.object(managed.subprocess, 'run', return_value=Mock(returncode=32, stderr='busy')):
            self.assertFalse(managed.stop_driver(process, Path('/mount'), {}))


if __name__ == '__main__':
    unittest.main()
