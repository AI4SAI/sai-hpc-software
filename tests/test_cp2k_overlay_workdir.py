import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import cp2k_overlay_workdir as helper


class WorkdirTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source.ext3'
        self.target = self.root / 'clone.ext3'
        self.results = self.root / 'results'
        self.results.mkdir()

    def checksum(self, path):
        fd = os.open(path, os.O_RDONLY)
        try:
            return helper.fd_checksum(fd)
        finally:
            os.close(fd)

    def image(self, extra=''):
        if not shutil.which('mke2fs') or not Path(helper.DEBUGFS).exists():
            self.skipTest('ext filesystem diagnostic tools unavailable')
        with self.source.open('xb') as stream:
            stream.truncate(16 * 1024 * 1024)
        subprocess.run(['mke2fs', '-q', '-F', '-t', 'ext3', str(self.source)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        empty = self.root / 'empty'
        empty.touch()
        payload = self.root / 'payload'
        payload.write_text('scientific payload survives cleanup\n')
        commands = ('mkdir /upper\nmkdir /work\nmkdir /work/work\n'
                    'mkdir /work/work/204\nwrite ' + str(empty) + ' /work/work/204/.wh..wh..opq\n'
                    'write ' + str(payload) + ' /upper/payload\n'
                    'write ' + str(payload) + ' /work/work/.fuse_hidden0000000100000002\n' + extra)
        result = subprocess.run([helper.DEBUGFS, '-w', '-f', '-', str(self.source)],
                                input=commands, text=True, capture_output=True, check=True)
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        self.source.chmod(0o400)
        return self.checksum(self.source)

    def prepare(self, expected=None):
        return helper.prepare_clone(self.source, self.target,
                                    expected or self.checksum(self.source), self.results)

    def test_real_clone_cleans_only_work_and_preserves_source_and_payload(self):
        expected = self.image()
        report = self.prepare(expected)
        self.assertEqual(report['status'], 'prepared')
        self.assertEqual(report['source_overlay_sha256'], self.checksum(self.source))
        self.assertEqual(report['prepared_overlay_sha256'], self.checksum(self.target))
        self.assertNotEqual(expected, self.checksum(self.target))
        self.assertEqual(len(report['removed_entries']), 3)
        output = subprocess.run([helper.DEBUGFS, '-R', 'cat /upper/payload', str(self.target)],
                                text=True, capture_output=True, check=True)
        self.assertEqual(output.stdout, 'scientific payload survives cleanup\n')
        self.assertEqual(json.loads((self.results / helper.RECEIPT).read_text()), report)
        self.assertEqual(sum(row['argv'][0] == helper.FSCK for row in report['commands']), 2)

    def test_unexpected_tree_and_symlink_are_rejected_before_mutation(self):
        for extra in ('mkdir /work/work/204/nested\n',
                      'symlink /work/work/.fuse_hidden0000000100000003 /upper/payload\n',
                      'mkdir /work/work/not-reviewed\n'):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as name:
                self.root = Path(name)
                self.source, self.target = self.root / 'source.ext3', self.root / 'clone.ext3'
                self.results = self.root / 'results'
                self.results.mkdir()
                expected = self.image(extra)
                with self.assertRaisesRegex(ValueError, 'unexpected'):
                    self.prepare(expected)
                self.assertEqual(self.checksum(self.target), expected)
                self.assertEqual(self.checksum(self.source), expected)
                self.assertEqual(json.loads((self.results / helper.RECEIPT).read_text())['status'], 'failed')

    def test_large_work_directory_deleted_slots_are_not_live_entries(self):
        expected = self.image(''.join('mkdir /work/work/%d\n' % i for i in range(1000, 1400)))
        report = self.prepare(expected)
        self.assertEqual(report['status'], 'prepared')
        self.assertEqual(len(report['removed_entries']), 403)
        self.assertEqual(report['remaining_entries'], 0)
        self.assertEqual(self.checksum(self.source), expected)

    def test_hardlink_to_upper_is_rejected_before_cleanup(self):
        expected = self.image('ln /upper/payload /work/work/.fuse_hidden0000000100000003\n'
                              'set_inode_field /upper/payload links_count 2\n')
        with self.assertRaisesRegex(ValueError, 'link count'):
            self.prepare(expected)
        self.assertEqual(self.checksum(self.target), expected)
        self.assertEqual(self.checksum(self.source), expected)

    def test_existing_target_bad_checksum_and_writable_source_are_rejected(self):
        self.source.write_bytes(b'original')
        self.source.chmod(0o400)
        self.target.write_bytes(b'existing')
        with self.assertRaisesRegex(ValueError, 'new clone'):
            self.prepare()
        self.assertEqual(self.target.read_bytes(), b'existing')
        self.target.unlink()
        with self.assertRaisesRegex(ValueError, 'changed before clone'):
            self.prepare('0' * 64)
        self.assertFalse(self.target.exists())
        self.results = self.root / 'writable-source-results'
        self.results.mkdir()
        self.source.chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'no writable mode bits'):
            self.prepare()
        self.assertFalse(self.target.exists())

    def test_ofd_lock_survives_other_descriptor_close_and_blocks_driver_locks(self):
        self.source.write_bytes(b'lock test')
        with self.source.open('r+b') as stream:
            helper.lock(stream.fileno(), True)
            self.source.read_bytes()  # OFD remains locked after unrelated close.
            for use_ofd in (False, True):
                code = ('import os,fcntl,sys;sys.path.insert(0,sys.argv[2]);'
                        'import cp2k_overlay_workdir as h;f=os.open(sys.argv[1],os.O_RDWR);'
                        + ('h.lock(f,True)' if use_ofd else
                           'fcntl.lockf(f,fcntl.LOCK_EX|fcntl.LOCK_NB)'))
                result = subprocess.run([sys.executable, '-B', '-c', code, str(self.source),
                                         str(Path(helper.__file__).parent)], capture_output=True)
                self.assertNotEqual(result.returncode, 0)
        with self.source.open('r+b') as stream:
            helper.lock(stream.fileno(), True)

    def test_debugfs_zero_exit_with_error_or_missing_response_is_rejected(self):
        self.source.write_bytes(b'fixture')
        directory = self.root / 'logs'
        directory.mkdir()
        with self.source.open('rb') as stream:
            inspector = helper.Inspector(stream.fileno(), directory, {'commands': []})
            for stdout, stderr in [('debugfs: rm /work/work/204\n', 'debugfs 1.47.0\nFile not found\n'),
                                   ('', 'debugfs 1.47.0\n')]:
                with self.subTest(stderr=stderr), patch.object(helper.subprocess, 'run',
                        return_value=subprocess.CompletedProcess([], 0, stdout, stderr)):
                    with self.assertRaises(ValueError):
                        inspector.debugfs(['rm /work/work/204'], writable=True)

    def test_listing_parser_rejects_control_bytes_ambiguous_sizes_and_duplicates(self):
        base = '/12/040755/0/0/.//\n/13/040755/0/0/..//\n'
        for tail in ('/20/100600/0/0/evil\tname/0/\n', '/20/100600/0/0/foo//\n',
                     '/20/040755/0/0/204/0/\n', '/12/040755/0/0/.//\n',
                     '/0/040755/0/0/204/0/\n', '/0/000000/1/0/204/0/\n',
                     '/0/000000/0/0/204/1/\n'):
            with self.subTest(tail=tail), self.assertRaises(ValueError):
                helper.parse_listing(base + tail)


if __name__ == '__main__':
    unittest.main()
