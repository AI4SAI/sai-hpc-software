from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'controller'))
from md_source_version import git, restore_release_tag, verify_deepmd_version


class ReleaseVersionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        git(self.root, 'init', '-q')
        git(self.root, 'config', 'user.name', 'Version fixture')
        git(self.root, 'config', 'user.email', 'version@example.invalid')
        (self.root / 'source.txt').write_text('unchanged upstream source\n')
        git(self.root, 'add', 'source.txt')
        git(self.root, 'commit', '-qm', 'source')
        self.source = dict(sha=git(self.root, 'rev-parse', 'HEAD'), track='release', ref='v3.2.0')

    def test_commit_only_checkout_recovers_exact_release_and_is_idempotent(self):
        before = git(self.root, 'rev-parse', 'HEAD^{tree}')
        self.assertEqual(git(self.root, 'tag', '--list'), '')
        self.assertTrue(restore_release_tag(self.root, self.source)['changed'])
        self.assertEqual(git(self.root, 'describe', '--tags', '--dirty'), 'v3.2.0')
        self.assertEqual(git(self.root, 'rev-parse', 'HEAD^{tree}'), before)
        self.assertFalse(restore_release_tag(self.root, self.source)['changed'])

    def test_wrong_commit_and_conflicting_tag_are_rejected_without_overwriting(self):
        git(self.root, 'tag', 'v3.2.0')
        git(self.root, 'commit', '--allow-empty', '-qm', 'another revision')
        other = git(self.root, 'rev-parse', 'HEAD')
        with self.assertRaisesRegex(ValueError, 'checkout differs'):
            restore_release_tag(self.root, self.source)
        with self.assertRaisesRegex(ValueError, 'existing release tag differs'):
            restore_release_tag(self.root, dict(self.source, sha=other))
        self.assertEqual(git(self.root, 'rev-parse', 'v3.2.0'), self.source['sha'])

    def test_development_is_never_given_a_release_tag(self):
        self.assertFalse(restore_release_tag(self.root, dict(self.source, track='development', ref='master'))['changed'])
        self.assertEqual(git(self.root, 'tag', '--list'), '')

    def test_release_metadata_rejects_sha_only_fallback_and_other_versions(self):
        self.assertEqual(verify_deepmd_version(self.source, '3.2.0')['expected'], '3.2.0')
        for version in ('0.1.dev1+g8cfd46e37', '3.1.2', '3.2.0+unreviewed', '3.2.0rc1'):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, 'version mismatch'):
                verify_deepmd_version(self.source, version)
        pre = dict(self.source, track='prerelease', ref='v3.3.0-rc.1')
        self.assertEqual(verify_deepmd_version(pre, '3.3.0rc1')['expected'], '3.3.0rc1')


if __name__ == '__main__':
    unittest.main()
