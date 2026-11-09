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


def settled(*paths):
    # Mounted filesystems can use a host clock slightly ahead of Python's clock.
    # Retain ccache's race checks; wait until these test inputs are old enough.
    newest = max(max(p.stat().st_ctime, p.stat().st_mtime) for p in paths)
    delay = max(0, newest - time.time() + 1.05)
    if delay > 30:
        raise RuntimeError('test filesystem clock differs by more than 30 seconds')
    time.sleep(delay)


def ready(config, *directories):
    return dict(config, ready=True, namespace=cache.include_layout(directories)['sha256'])


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
        self.assertEqual(env['CCACHE_NODEPEND'], 'true')
        self.assertNotIn('CCACHE_DEPEND', env)

    def test_unknown_arguments_and_unfinalized_configuration_compile_without_cache(self):
        for option in ('@response', '-Wp,-MD,file', '-fmodules', '-include-pch'):
            args = ['-c', 'a.cpp', option]
            self.assertFalse(cache.cacheable(args))
            with patch.object(cache.subprocess, 'call', return_value=0) as run:
                cache.compile_command(dict(ready=True), '/compiler', args)
                run.assert_called_once_with(['/compiler', *args])
        with patch.object(cache.subprocess, 'call', return_value=0) as run:
            cache.compile_command(dict(ready=False), '/compiler', ['-c', 'a.cpp'])
            run.assert_called_once_with(['/compiler', '-c', 'a.cpp'])

    def test_layout_tracks_names_and_symlinks_but_not_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            include = root / 'include'; include.mkdir()
            header = include / 'extensionless'; header.write_text('one')
            original = cache.include_layout([include])
            header.write_text('two')
            self.assertEqual(original, cache.include_layout([include]))
            alias = include / 'alias'; alias.symlink_to(header)
            linked = cache.include_layout([include])
            self.assertNotEqual(original['sha256'], linked['sha256'])
            # A directory symlink cycle must terminate, while names under the
            # linked directory must still participate in the inventory.
            (include / 'loop').symlink_to(include, target_is_directory=True)
            before = cache.include_layout([include])
            (include / 'new.h').write_text('three')
            self.assertNotEqual(before['sha256'], cache.include_layout([include])['sha256'])

    def test_compile_database_covers_quote_system_cuda_and_default_include_paths(self):
        rows = [dict(directory='/build', file='/source/a.cpp', arguments=[
            'nvcc_wrapper', '-Irelative', '-isystem', '/system', '-iquote/quotes',
            '-idirafter', '/after', '--include-path=/cuda/one,/cuda/two', '-c', '/source/a.cpp'])]
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(cache.include_dirs(rows, ['/default']), {
                '/source', '/build/relative', '/system', '/quotes', '/after',
                '/cuda/one', '/cuda/two', '/default'})

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
            config = ready(config, root)
            settled(source, wrapper)
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

    def test_native_content_checks_and_layout_guard_return_current_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool = os.environ.get('CCACHE_TEST_BINARY') or shutil.which('ccache')
            config = dict(tool=tool, store=str(root / 'cache'), temporary=str(root / 'tmp'),
                          context=str(root / 'context'), stats_log=str(root / 'stats'))
            (root / 'tmp').mkdir()
            (root / 'context').write_text('compiler-and-partition-A')
            (root / 'early').mkdir(); (root / 'late').mkdir(); (root / 'src').mkdir()
            header = root / 'late/value.h'
            header.write_text('#define VALUE 11\n')
            source = root / 'src/a.cpp'
            source.write_text('#include "value.h"\n#ifndef OFFSET\n#define OFFSET 0\n#endif\n'
                              'int value() { return VALUE + OFFSET; }\n')
            other = root / 'src/b.cpp'
            other.write_text('int other() {return 33;}\n')
            config = ready(config, root / 'src', root / 'early', root / 'late')
            settled(source, other, header)

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
            settled(header)
            hit, changed = build(source)
            self.assertFalse(hit); self.assertNotEqual(baseline, changed)
            self.assertTrue(build(other)[0])
            self.assertTrue(build(source)[0])
            # Native preprocessor fallback may safely reuse equivalent output.
            source.write_text(source.read_text() + '// comment changed\n')
            settled(source)
            hit, commented = build(source)
            self.assertTrue(hit); self.assertEqual(changed, commented)
            self.assertTrue(build(source)[0])
            self.assertTrue(build(other)[0])
            hit, flags = build(source, '-DOFFSET=1')
            self.assertFalse(hit); self.assertNotEqual(changed, flags)
            self.assertTrue(build(source, '-DOFFSET=1')[0])
            # An earlier include directory gaining a header must not return an
            # old direct-mode hit just because its previously used file exists.
            (root / 'early/value.h').write_text('#define VALUE 44\n')
            config = ready(config, root / 'src', root / 'early', root / 'late')
            settled(root / 'early/value.h')
            hit, shadowed = build(source)
            self.assertFalse(hit); self.assertNotEqual(changed, shadowed)
            # A file-set change conservatively starts a new namespace for the
            # entire build. Content-only changes above retain unrelated hits.
            self.assertFalse(build(other)[0])
            self.assertTrue(build(other)[0])
            (root / 'context').write_text('compiler-and-partition-B')
            self.assertFalse(build(other)[0])

    def test_mmd_still_checks_system_header_contents(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'tmp').mkdir(); (root / 'src').mkdir(); (root / 'system').mkdir()
            context = root / 'context'; context.write_text('toolchain')
            header = root / 'system/value.h'; header.write_text('#define VALUE 11\n')
            source = root / 'src/a.cpp'
            source.write_text('#include <value.h>\nint value() {return VALUE;}\n')
            config = ready(dict(tool=os.environ.get('CCACHE_TEST_BINARY') or shutil.which('ccache'),
                store=str(root / 'cache'), temporary=str(root / 'tmp'), context=str(context),
                stats_log=str(root / 'stats')), root / 'src', root / 'system')
            args = ['-isystem', str(root / 'system'), '-MMD', '-MF', str(root / 'a.d'),
                    '-c', str(source), '-o', str(root / 'a.o')]
            settled(source, header)
            for expected in (False, True):
                Path(config['stats_log']).unlink(missing_ok=True)
                self.assertEqual(cache.compile_command(config, shutil.which('g++'), args), 0)
                self.assertEqual('direct_cache_hit' in Path(config['stats_log']).read_text(), expected)
            previous = header.stat()
            header.write_text('#define VALUE 22\n')
            os.utime(header, ns=(previous.st_atime_ns, previous.st_mtime_ns))
            settled(header)
            Path(config['stats_log']).unlink()
            self.assertEqual(cache.compile_command(config, shutil.which('g++'), args), 0)
            self.assertNotIn('cache_hit', Path(config['stats_log']).read_text())


if __name__ == '__main__':
    unittest.main()
