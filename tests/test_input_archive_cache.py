from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'controller'))
import ci
import input_archive_cache as cache


class InputArchiveCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / '.test-work')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.name = 'libxc-7.1.2.tar.bz2'
        self.data = b'validated dependency archive bytes' * 100
        self.digest = hashlib.sha256(self.data).hexdigest()
        self.directory = self.root / 'runs/run-one/input/cp2k-updates'
        self.cached = self.root / 'cache/source-archives' / self.digest / self.name

    def upload(self, directory=None, token='a'):
        directory = directory or self.directory
        directory.mkdir(parents=True, exist_ok=True)
        upload = directory / ('.upload-' + token * 32 + '-' + self.name)
        upload.write_bytes(self.data)
        return upload

    def test_miss_then_commit_then_independent_cache_hit(self):
        self.assertFalse(cache.stage(self.root, self.directory, self.name, self.digest))
        upload = self.upload()
        self.assertTrue(cache.stage(self.root, self.directory, self.name, self.digest, upload=upload))
        self.assertFalse(upload.exists())
        second = self.root / 'runs/run-two/input/cp2k-updates'
        self.assertTrue(cache.stage(self.root, second, self.name, self.digest))
        paths = [self.cached, self.directory / self.name, second / self.name]
        self.assertEqual(len({p.stat().st_ino for p in paths}), 3)
        for path in paths:
            self.assertEqual(path.read_bytes(), self.data)
            self.assertEqual(path.stat().st_nlink, 1)
            self.assertEqual(path.stat().st_mode & 0o222, 0)

    def test_damaged_upload_does_not_publish_or_touch_good_cache(self):
        upload = self.upload()
        upload.write_bytes(b'partial upload')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            cache.stage(self.root, self.directory, self.name, self.digest, upload=upload)
        self.assertFalse(self.cached.exists())
        self.assertFalse((self.directory / self.name).exists())
        self.assertTrue(upload.exists())

    def test_bad_cache_or_existing_input_is_not_silently_repaired(self):
        upload = self.upload()
        cache.stage(self.root, self.directory, self.name, self.digest, upload=upload)
        for path in (self.cached, self.directory / self.name):
            with self.subTest(path=path):
                path.chmod(0o644)
                path.write_bytes(b'corruption')
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    cache.stage(self.root, self.directory, self.name, self.digest)
                self.assertEqual(path.read_bytes(), b'corruption')
                path.write_bytes(self.data)
                path.chmod(0o444)

    def test_parallel_publish_never_shares_inodes_or_truncates(self):
        jobs = []
        for index in range(8):
            directory = self.root / f'runs/run-{index}/input/abacus-updates'
            jobs.append((directory, self.upload(directory, token=hex(index)[2:])))
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(cache.stage, self.root, directory, self.name,
                                       self.digest, upload=upload) for directory, upload in jobs]
            self.assertTrue(all(future.result() for future in futures))
        paths = [self.cached] + [directory / self.name for directory, _ in jobs]
        self.assertEqual(len({p.stat().st_ino for p in paths}), 9)
        self.assertTrue(all(p.read_bytes() == self.data for p in paths))
        self.assertFalse([p for p in self.root.glob('runs/*/input/*/.archive-*')
                          if p.name != '.archive-stage.lock'])

    def test_symlinks_and_escaping_paths_are_rejected(self):
        source = self.root / 'seed'
        source.write_bytes(self.data)
        upload = self.upload()
        upload.unlink()
        upload.symlink_to(source)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            cache.stage(self.root, self.directory, self.name, self.digest, upload=upload)
        for directory, name in ((self.root / 'elsewhere', self.name),
                                (self.directory, '../escape'),
                                (self.directory, '/absolute'),
                                (self.directory / '..', self.name)):
            with self.subTest(directory=directory, name=name), self.assertRaises(ValueError):
                cache.stage(self.root, directory, name, self.digest)
        bucket = self.cached.parent
        # A redirected cache parent must fail before opening or copying files.
        redirected = self.root / 'redirected'
        redirected.mkdir()
        other = 'b' * 64
        (bucket.parent / other).symlink_to(redirected, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            cache.stage(self.root, self.directory, self.name, other)

    def test_seed_is_a_verified_independent_copy(self):
        source = self.root / 'old-archive'
        source.write_bytes(self.data)
        self.assertEqual(cache.seed(self.root, source, self.name, self.digest), self.cached)
        self.assertEqual(source.read_bytes(), self.data)
        self.assertNotEqual(source.stat().st_ino, self.cached.stat().st_ino)


class ArchiveTransportTests(unittest.TestCase):
    def test_cache_hit_uploads_nothing(self):
        remote = Mock(return_value=subprocess.CompletedProcess([], 0, stdout='true'))
        upload = Mock()
        ci.stage_archives(remote, upload, '/project', '/project/runs/run/input/cp2k-updates',
                          [(Path('/runner/libxc.tar.bz2'), 'a' * 64)])
        upload.assert_not_called()
        self.assertEqual(remote.call_count, 1)

    def test_cache_misses_have_unique_uploads_then_verified_commit(self):
        remote = Mock(side_effect=[subprocess.CompletedProcess([], 0, stdout=json.dumps(value))
                                   for value in (False, True, False, True)])
        upload = Mock()
        arguments = (remote, upload, '/project', '/project/runs/run/input/cp2k-updates',
                     [(Path('/runner/libxc.tar.bz2'), 'a' * 64)])
        ci.stage_archives(*arguments)
        ci.stage_archives(*arguments)
        paths = [call.args[1] for call in upload.call_args_list]
        self.assertEqual(len(set(paths)), 2)
        self.assertTrue(all('/.upload-' in path for path in paths))
        for index, path in enumerate(paths):
            self.assertEqual(remote.call_args_list[index * 2 + 1].args[-2:], ('--upload', path))


if __name__ == '__main__':
    unittest.main()
