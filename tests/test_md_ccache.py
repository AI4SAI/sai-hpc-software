import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'controller'))
import md_ccache as cache
import md_lammps_standalone as standalone


class CachePolicyTests(unittest.TestCase):
    def test_only_build_has_a_writable_cache_and_it_is_partition_specific(self):
        for target in cache.TARGETS:
            delivery = standalone.make_delivery(dict(track='development', source_ref='develop',
                source_sha='a' * 40, source_version='develop'), target)
            lines = standalone.render(delivery, 'cache-policy').splitlines()
            bound = [line for line in lines if ':/ccache-store:rw' in line]
            self.assertEqual(len(bound), 1)
            self.assertIn(' build ', bound[0])
            self.assertIn('/cache/compiler/lammps/' + target + ':/ccache-store:rw', bound[0])
            self.assertIn(':/ccache-tool:ro', bound[0])
            self.assertIn('--env SAI_MD_CCACHE=1', bound[0])

    def test_environment_cannot_weaken_input_checks(self):
        config = dict(store='/cache', temporary='/scratch', stats_log='/stats', context='/context')
        env = cache.cache_environment(config, 'inputs', dict(PATH='/usr/bin',
            CCACHE_SLOPPINESS='time_macros,system_headers,file_stat_matches',
            CCACHE_COMPILERCHECK='none', CCACHE_IGNOREHEADERS='/usr/include',
            CCACHE_REMOTE_STORAGE='http://unexpected'))
        self.assertEqual(env['CCACHE_COMPILERCHECK'], 'content')
        self.assertEqual(env['CCACHE_SLOPPINESS'], '')
        self.assertNotIn('CCACHE_IGNOREHEADERS', env)
        self.assertNotIn('CCACHE_REMOTE_STORAGE', env)
        self.assertEqual(env['CCACHE_NOINODECACHE'], 'true')

    def test_unknown_arguments_bypass_and_scanning_does_not_omit_system_headers(self):
        for option in ('@response', '-Wp,-MD,file', '-fmodules', '-include-pch'):
            self.assertIsNone(cache.dependency_arguments(['-c', 'a.cpp', option], '/dep'))
        args = cache.dependency_arguments(['-c', 'a.cpp', '-MMD', '-MF', 'a.d', '-MT', 'a.o',
                                           '-o', 'a.o', '-Iheaders', '-DVALUE=2'], '/dep')
        self.assertEqual(args, ['a.cpp', '-Iheaders', '-DVALUE=2', '-M', '-o', '/dep',
                                '-MT', 'sai_ccache_dependencies'])

    def test_tool_checksum_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / 'ccache'
            file.write_bytes(b'wrong compiler cache')
            with self.assertRaisesRegex(ValueError, 'pinned upstream'):
                cache.verify_tool(file)


@unittest.skipUnless(shutil.which('g++') and (os.environ.get('CCACHE_TEST_BINARY') or shutil.which('ccache')),
                     'requires g++ and ccache for real object-cache invalidation tests')
class RealCompilerCacheTests(unittest.TestCase):
    def test_native_cpu_flags_survive_a_wrapper_without_gcc_query_support(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool = os.environ.get('CCACHE_TEST_BINARY') or shutil.which('ccache')
            config = dict(tool=tool, store=str(root / 'cache'), temporary=str(root / 'tmp'),
                          context=str(root / 'context'), stats_log=str(root / 'stats'))
            (root / 'tmp').mkdir()
            (root / 'context').write_text('expanded-native-cpu-options-A')
            source = root / 'a.cpp'
            source.write_text('int answer() {return 42;}\n')
            wrapper = root / 'nvcc_wrapper'
            wrapper.write_text('#!/bin/sh\nif [ "$1" = "-###" ]; then exit 93; fi\nexec ' +
                               shutil.which('g++') + ' "$@"\n')
            wrapper.chmod(0o755)
            time.sleep(1.05)
            args = ['-march=native', '-mtune=native', '-MD', '-MF', str(root / 'a.d'),
                    '-c', str(source), '-o', str(root / 'a.o')]
            for expected in (False, True):
                Path(config['stats_log']).unlink(missing_ok=True)
                self.assertEqual(cache.compile_command(config, str(wrapper), args), 0)
                stats = Path(config['stats_log']).read_text()
                self.assertNotIn('internal_error', stats)
                self.assertEqual('direct_cache_hit' in stats, expected)
            (root / 'context').write_text('expanded-native-cpu-options-B')
            Path(config['stats_log']).unlink()
            self.assertEqual(cache.compile_command(config, str(wrapper), args), 0)
            self.assertNotIn('direct_cache_hit', Path(config['stats_log']).read_text())

    def test_raw_inputs_flags_context_and_new_include_invalidate_only_affected_objects(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool = os.environ.get('CCACHE_TEST_BINARY') or shutil.which('ccache')
            config = dict(tool=tool, store=str(root / 'cache'), temporary=str(root / 'tmp'),
                          context=str(root / 'context'), stats_log=str(root / 'stats'))
            (root / 'tmp').mkdir()
            (root / 'context').write_text('compiler-and-partition-A')
            (root / 'early').mkdir(); (root / 'late').mkdir()
            header = root / 'late/value.h'
            header.write_text('#define VALUE 11\n')
            source = root / 'a.cpp'
            source.write_text('#include "value.h"\nint value() { return VALUE; }\n')
            other = root / 'b.cpp'
            other.write_text('int other() {return 33;}\n')
            # Retain the real race checks even on coarse timestamp filesystems.
            time.sleep(1.05)

            def build(path, *flags):
                log = Path(config['stats_log']); log.unlink(missing_ok=True)
                obj = root / (path.stem + '.o'); obj.unlink(missing_ok=True)
                args = ['-O2', '-I' + str(root / 'early'), '-I' + str(root / 'late'),
                        '-MD', '-MF', str(root / (path.stem + '.d')), '-c', str(path),
                        '-o', str(obj), *flags]
                self.assertEqual(cache.compile_command(config, shutil.which('g++'), args), 0)
                stats = log.read_text()
                return ('direct_cache_hit' in stats or 'preprocessed_cache_hit' in stats), obj.read_bytes()

            self.assertFalse(build(source)[0]); self.assertFalse(build(other)[0])
            hit, baseline = build(source); self.assertTrue(hit)
            self.assertTrue(build(other)[0])
            # Same length and restored mtime must not mask a content change.
            original = header.stat()
            header.write_text('#define VALUE 22\n')
            os.utime(header, ns=(original.st_atime_ns, original.st_mtime_ns))
            time.sleep(1.05)
            hit, changed = build(source)
            self.assertFalse(hit); self.assertNotEqual(baseline, changed)
            self.assertTrue(build(other)[0])
            self.assertTrue(build(source)[0])
            # Even a comment-only change must miss; ordinary preprocessor
            # caching would otherwise consider the result interchangeable.
            source.write_text(source.read_text() + '// comment changed\n')
            time.sleep(1.05)
            self.assertFalse(build(source)[0]); self.assertTrue(build(source)[0])
            self.assertTrue(build(other)[0])
            self.assertFalse(build(source, '-DNEW_OPTION=1')[0])
            self.assertTrue(build(source, '-DNEW_OPTION=1')[0])
            # An earlier include directory gaining a header must not return an
            # old direct-mode hit just because its previously used file exists.
            (root / 'early/value.h').write_text('#define VALUE 44\n')
            time.sleep(1.05)
            hit, shadowed = build(source)
            self.assertFalse(hit); self.assertNotEqual(changed, shadowed)
            self.assertTrue(build(other)[0])
            (root / 'context').write_text('compiler-and-partition-B')
            self.assertFalse(build(other)[0])


if __name__ == '__main__':
    unittest.main()
