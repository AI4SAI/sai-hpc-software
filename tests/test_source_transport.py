import json
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import source_cache as cache
import source_transport as transport


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'source'
        self.repo.mkdir()
        cache.git(self.repo, 'init')
        cache.git(self.repo, 'config', 'user.name', 'Test')
        cache.git(self.repo, 'config', 'user.email', 'test@example.invalid')
        (self.repo / 'data').write_text('verified source')
        cache.git(self.repo, 'add', 'data')
        cache.git(self.repo, 'commit', '-m', 'base')
        self.commit = cache.git(self.repo, 'rev-parse', 'HEAD')
        self.store = self.root / 'cache.git'
        self.argv = [sys.executable, str(Path(cache.__file__)), 'transfer-session',
                     str(self.store), self.commit]

    def start(self):
        p = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        def cleanup():
            if p.poll() is None:
                p.kill()
            p.communicate(timeout=5)
        self.addCleanup(cleanup)
        return p

    def test_concurrent_same_source_transfers_coalesce_after_verified_receive(self):
        first = self.start()
        self.assertEqual(transport.response(first, 5), {'cache_shas': []})
        second = self.start()
        self.assertFalse(select.select([second.stdout], [], [], 0.2)[0])
        parts = self.root / 'parts'
        cache.pack(self.repo, self.commit, parts)
        first.stdin.write(json.dumps({'directory': str(parts)}) + '\n')
        first.stdin.flush()
        self.assertEqual(transport.response(first, 5), {'received': self.commit})
        self.assertEqual(first.wait(timeout=5), 0)
        self.assertIn(self.commit, transport.response(second, 5)['cache_shas'])
        self.assertEqual(second.wait(timeout=5), 0)
        self.assertEqual(cache.git(self.store, 'show', self.commit + ':data'), 'verified source')

    def test_disconnected_sender_releases_lease(self):
        first = self.start()
        transport.response(first, 5)
        second = self.start()
        self.assertFalse(select.select([second.stdout], [], [], 0.2)[0])
        first.stdin.close()
        first.stdin = None
        self.assertEqual(first.wait(timeout=5), 0)
        self.assertEqual(transport.response(second, 5), {'cache_shas': []})
        second.stdin.close()
        second.stdin = None
        self.assertEqual(second.wait(timeout=5), 0)

    def test_wrong_source_is_not_published_by_lease(self):
        parts = self.root / 'parts'
        cache.pack(self.repo, self.commit, parts)
        with self.assertRaisesRegex(ValueError, 'differs from the transfer lease'):
            cache.receive(self.store, parts, expected_commit='f' * 40)
        self.assertFalse(cache.complete(self.store, self.commit))

    @unittest.skipUnless(shutil.which('rsync'), 'requires rsync')
    def test_retry_repairs_partial_and_equal_size_corruption(self):
        # Run the real rsync remote protocol over a local shell transport.
        shell = self.root / 'local-ssh'
        shell.write_text('#!/bin/sh\nshift\nexec "$@"\n')
        shell.chmod(0o755)
        source, dest = self.root / 'source.part.00', self.root / 'destination part'
        source.write_bytes(bytes(range(256)) * 4096)
        calls = []
        def interrupted(argv, **kwargs):
            calls.append(argv)
            if len(calls) == 1:
                dest.write_bytes(source.read_bytes()[:10003])
                raise subprocess.TimeoutExpired(argv, 900)
            return subprocess.run(argv, **kwargs)
        transport.upload_part(source, str(dest), 'local', [str(shell)],
                              run=interrupted, pause=lambda _: None)
        self.assertEqual(len(calls), 2)
        self.assertEqual(cache.checksum(source), cache.checksum(dest))
        dest.write_bytes(b'x' * source.stat().st_size)
        transport.upload_part(source, str(dest), 'local', [str(shell)])
        self.assertEqual(cache.checksum(source), cache.checksum(dest))
