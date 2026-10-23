"""Independent LAMMPS CI freezes its source and requires GPU science after build."""
from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'controller'))
import md_lammps_ci as ci
from release_contract import claim_build, resolve_tracks


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
    def exercise(self, runner, history, *, track='development', attempt='1', retry='false', failure=None):
        commands, uploads = [], []
        environment = dict(LAMMPS_SOURCE=json.dumps(dict(source(track), target='16v100-avx2')), LAMMPS_TARGET='16v100-avx2',
            GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT=attempt, GITHUB_SHA='b' * 40,
            REMOTE_USER='unit', RUNNER_TEMP=str(runner), RETRY_RELEASES=retry)
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
            return subprocess.CompletedProcess(argv, 0, '')
        output = io.StringIO()
        with patch.dict(os.environ, environment, clear=True), patch.object(ci, 'run', side_effect=remote), \
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
            self.assertEqual(len(uploads), 1)
            for name in ('lammps_acceptance.py', 'lammps_rank.sh', 'lammps_verify.sbatch',
                         'native-reference.json', 'runtime-plan.json', 'delivery.json'):
                self.assertTrue(any(Path(arg).name == name for arg in uploads[0][:-1]))
            self.assertIn('LAMMPS_GPU_SCIENCE_ACCEPTED', output)

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

    def test_build_or_scientific_failure_never_reports_accepted(self):
        for failure in (('md_lammps_standalone.py', 'monitor'), ('lammps_acceptance.py', 'verify')):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                commands, _, output = self.exercise(root, root / 'history', failure=failure)
                self.assertNotIn('LAMMPS_GPU_SCIENCE_ACCEPTED', output)
                if failure[0] == 'md_lammps_standalone.py':
                    self.assertFalse(any(name == 'lammps_acceptance.py' for name, args in commands))


if __name__ == '__main__':
    unittest.main()
