"""Integrity and overwrite guards for the administrator's dated LTS snapshot."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1] / 'deployment/abacus-lts-20261120'
SPEC = importlib.util.spec_from_file_location('abacus_lts_install', PACKAGE / 'install.py')
deployment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deployment)


class LTSDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.release = deployment.load_release()
        self.plan = json.loads((PACKAGE / 'runtime-plan.json').read_text())

    def test_all_four_snapshot_identities_and_runtime_trees_validate(self):
        entries = deployment.runtime_plan(self.release)
        self.assertEqual(set(entries), set(deployment.PARTITIONS))
        for entry in entries.values():
            files = [r['path'] for r in entry['files'] if r['kind'] == 'file']
            self.assertEqual(len(files), 16)
            self.assertTrue(all(deployment.runtime_file(name) for name in files))
            self.assertFalse(any('share/sai' in name or name.endswith('.a') for name in files))

    def test_different_branch_commit_recipe_and_relocated_prefix_rejected(self):
        original = self.release['deliveries'][0]['identity']
        for key, value in [('source_ref', 'develop'), ('source_sha', 'a'*40),
                           ('recipe_sha256', 'b'*64), ('install_prefix', '/opt/apps/abacus/old')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                deployment.validate_identity(dict(original, **{key: value}), original['partition'])

    def test_changed_module_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'release.json').write_bytes((PACKAGE / 'release.json').read_bytes())
            (root / deployment.MODULE.name).write_text('changed')
            with patch.object(deployment, 'PACKAGE', root), self.assertRaisesRegex(ValueError, 'module changed'):
                deployment.load_release()

    def test_runtime_plan_rejects_traversal_headers_and_dangling_parent(self):
        for path in ('../../other', '/opt/other', 'dependencies/libtorch/include/header.h',
                     'dependencies/libtorch/new/libextra.so'):
            plan = copy.deepcopy(self.plan)
            row = next(r for r in plan['installations'][0]['files'] if r['kind'] == 'file')
            row['path'] = path
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root/'release.json').write_bytes((PACKAGE/'release.json').read_bytes())
                (root/'runtime-plan.json').write_text(json.dumps(plan))
                with self.subTest(path=path), patch.object(deployment, 'PACKAGE', root), self.assertRaises(ValueError):
                    deployment.runtime_plan(self.release)

    def test_no_path_can_escape_the_handoff_package(self):
        for name in ('/etc/passwd', '../images/other', 'images/../../other'):
            with self.subTest(path=name), self.assertRaisesRegex(ValueError, 'unsafe package path'):
                deployment.package_file(name)

    def test_existing_runtime_changes_and_extra_files_are_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o755)
            path = root/'abacus'
            path.write_bytes(b'accepted')
            path.chmod(0o755)
            rows = {'.': dict(path='.', kind='directory', mode=0o755),
                    'abacus': dict(path='abacus', kind='file', mode=0o755, size=8,
                                   sha256=hashlib.sha256(b'accepted').hexdigest())}
            deployment.verify_records(root, rows)
            path.write_bytes(b'changed!')
            with self.assertRaisesRegex(ValueError, 'payload changed'):
                deployment.verify_records(root, rows)
            path.write_bytes(b'accepted')
            (root/'extra').write_text('extra')
            with self.assertRaisesRegex(ValueError, 'unlisted file'):
                deployment.verify_records(root, rows)

    def test_symlink_installation_ancestor_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'real').mkdir()
            (root/'linked').symlink_to(root/'real', target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                deployment.plain(root/'linked/bin/abacus')


if __name__ == '__main__':
    unittest.main()
