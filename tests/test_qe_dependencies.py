import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'controller'))
import qe_dependencies as dependencies


class OfflineDependenciesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.module = self.root/'upstream'
        self.parent = self.root/'parent'
        for repository in (self.module, self.parent):
            repository.mkdir()
            self.git(repository, 'init', '-q')
        (self.module/'source.f90').write_text('program example\nend program\n')
        (self.module/'run').write_text('#!/bin/sh\nexit 0\n')
        (self.module/'run').chmod(0o755)
        (self.module/'link').symlink_to('source.f90')
        self.git(self.module, 'add', '.')
        self.git(self.module, 'commit', '-qm', 'fixture')
        self.module_sha = self.git(self.module, 'rev-parse', 'HEAD')
        (self.parent/'external').mkdir()
        (self.parent/'external/submodule_commit_hash_records').write_text(self.module_sha+' devxlib\n')
        (self.parent/'.gitmodules').write_text(
            '[submodule "external/devxlib"]\npath = external/devxlib\nurl = https://offline.invalid/devxlib.git\n')
        self.git(self.parent, 'add', '.')
        self.git(self.parent, 'update-index', '--add', '--cacheinfo',
                 '160000,'+self.module_sha+',external/devxlib')
        self.git(self.parent, 'commit', '-qm', 'parent')
        self.parent_sha = self.git(self.parent, 'rev-parse', 'HEAD')
        self.patch = patch.dict(dependencies.REPOSITORIES, {'devxlib': self.module.as_uri()}, clear=True)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    @staticmethod
    def git(repository, *args):
        return subprocess.check_output(['git', '-C', str(repository), '-c', 'core.hooksPath=/dev/null',
            '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', *args], text=True).strip()

    def test_repeated_pack_is_identical_and_original_git_tree_works_offline(self):
        first = dependencies.prepare(self.parent, self.parent_sha, self.root/'first')
        second = dependencies.prepare(self.parent, self.parent_sha, self.root/'second')
        self.assertEqual(first[0][1], second[0][1])
        tree = self.root/'second/tree'
        for path in tree.rglob('*'):
            if not path.is_symlink():
                os.utime(path, (12345, 12345))
        repacked = self.root/'another-name.tar.gz'
        dependencies.archive_tree(tree, repacked)
        self.assertEqual(first[0][1], hashlib.sha256(repacked.read_bytes()).hexdigest())
        self.module.rename(self.root/'upstream-unavailable')
        dependencies.populate(self.parent, self.root/'first')
        module = self.parent/'external/devxlib'
        self.assertEqual(self.git(module, 'rev-parse', 'HEAD'), self.module_sha)
        self.assertEqual(self.git(module, 'status', '--porcelain'), '')
        self.assertTrue(os.access(module/'run', os.X_OK))
        self.assertEqual((module/'link').readlink(), Path('source.f90'))
        # The upstream CMake operation must not need network or missing objects.
        self.git(self.parent, 'submodule', 'init', '--', 'external/devxlib')
        self.git(self.parent, 'submodule', 'update', '--depth', '5', '--', 'external/devxlib')

    def test_extracted_worktree_must_match_its_pinned_git_objects(self):
        archives = dependencies.prepare(self.parent, self.parent_sha, self.root/'input')
        tree = self.root/'input/tree'
        (tree/'external/devxlib/source.f90').write_text('changed scientific source\n')
        archive = archives[0][0]
        archive.unlink()
        dependencies.archive_tree(tree, archive)
        manifest_path = self.root/'input/qe-submodules.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'pinned Git tree'):
            dependencies.populate(self.parent, self.root/'input')

    def test_unchanged_dependencies_share_cache_name_across_parent_revisions(self):
        first = dependencies.prepare(self.parent, self.parent_sha, self.root/'old')
        (self.parent/'README').write_text('new QE revision, identical submodules\n')
        self.git(self.parent, 'add', 'README')
        self.git(self.parent, 'commit', '-qm', 'next source')
        current = self.git(self.parent, 'rev-parse', 'HEAD')
        second = dependencies.prepare(self.parent, current, self.root/'new')
        self.assertEqual(first[0][0].name, second[0][0].name)
        self.assertEqual(first[0][1], second[0][1])
        self.assertNotEqual(first[1][1], second[1][1])


if __name__ == '__main__':
    unittest.main()
