"""Keep a release's named-user read grants from widening unrelated access."""
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1] / 'deployment/lammps-30Sep2026'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


with patch.dict(sys.modules, install=load('access_install', PACKAGE / 'install.py')):
    access = load('lammps_source_access', PACKAGE / 'source-access.py')


class SourceAccessTests(unittest.TestCase):
    def setUp(self):
        self.project = Path('/home/source/software')
        self.path = self.project / 'runs/release/results'
        self.account = SimpleNamespace(pw_uid=os.getuid() + 1)
        self.row = dict(passed=False, owner=os.getuid(), required=1, mode=0o700, acl='original',
                        entries={('user', ''): 7, ('group', ''): 0, ('other', ''): 0})

    def test_grant_only_named_user_traversal_preserves_private_group(self):
        with patch.object(access.subprocess, 'run') as run:
            changes = access.grant({self.path: self.row}, self.account, self.project, [])
        self.assertEqual(len(changes), 1)
        run.assert_called_once_with(['setfacl', '--no-mask', '-m',
            f'u:{self.account.pw_uid}:--x,m::--x', '--', str(self.path)], check=True)

    def test_mask_expansion_cannot_activate_another_users_hidden_access(self):
        self.row['entries'][('mask', '')] = 0
        self.row['entries'][('user', str(self.account.pw_uid + 1))] = 7
        with patch.object(access.subprocess, 'run') as run, self.assertRaisesRegex(ValueError, 'another principal'):
            access.grant({self.path: self.row}, self.account, self.project, [])
        run.assert_not_called()

    def test_frozen_export_modes_cannot_be_changed(self):
        with patch.object(access.subprocess, 'run') as run, self.assertRaisesRegex(ValueError, 'frozen export mode'):
            access.grant({self.path: self.row}, self.account, self.project, [self.project / 'runs'])
        run.assert_not_called()

    def test_target_failure_rejects_all_changes_before_any_grant(self):
        records = {self.path: self.row, Path('/opt/apps/lammps'): dict(self.row, required=7)}
        with patch.object(access.subprocess, 'run') as run, self.assertRaisesRegex(ValueError, 'outside owned project'):
            access.grant(records, self.account, self.project, [])
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
