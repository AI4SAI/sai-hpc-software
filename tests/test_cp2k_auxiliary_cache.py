from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'controller'))
import cp2k_auxiliary_cache as cache


class AuxiliaryCacheTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / '.test-work'
        parent.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.prefix = self.root / 'first-version'
        self.workspace = self.root / 'workspace'
        self.bundle = self.root / 'bundle'
        self.spec = dict(schema=1, target='16v100-avx2', sources=dict(wignernj=False),
                         native_flags='znver3', provider='a'*64)
        for part in ('dbcsr', 'tblite', 'libxs'):
            directory = self.prefix / 'dependencies' / part
            (directory / 'lib/cmake').mkdir(parents=True)
            (directory / 'include').mkdir()
            (directory / 'include/header.h').write_text('int answer(void);\n')
            (directory / 'lib/libexample.a').write_bytes(b'!<arch>\nstatic objects\x00\xff')
            (directory / 'lib/cmake/example.cmake').write_text('set(prefix "' + str(self.prefix) + '")\n')
        for name in cache.RECEIPTS:
            path = self.workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('export LIBXC_ROOT=' + str(self.prefix) + '/dependencies/libxc\n'
                            if name.endswith('.sh') else 'source evidence\n')

    def pack(self):
        return cache.pack(self.prefix, self.workspace, self.bundle, self.spec,
                          dict(source_sha='b'*40, prefix=str(self.prefix)))

    def accept(self):
        record = cache.read(self.bundle / 'manifest.json')
        cache.write(self.bundle / 'accepted.json', dict(science_verified=True, key=record['key'],
                    manifest_sha256=cache.checksum(self.bundle / 'manifest.json'),
                    archive_sha256=record['archive_sha256']))

    def destination(self):
        prefix, work = self.root / 'different-longer-version', self.root / 'fresh-workspace'
        prefix.mkdir(exist_ok=True)
        work.mkdir(exist_ok=True)
        return prefix, work

    def test_restore_preserves_archives_headers_and_relocates_only_metadata(self):
        original = (self.prefix / 'dependencies/dbcsr/lib/libexample.a').read_bytes()
        self.pack()
        self.accept()
        new, work = self.destination()
        cache.unpack(self.bundle, new, work, self.spec)
        self.assertEqual((new / 'dependencies/dbcsr/lib/libexample.a').read_bytes(), original)
        self.assertEqual((new / 'dependencies/dbcsr/include/header.h').read_text(), 'int answer(void);\n')
        self.assertEqual((new / 'dependencies/dbcsr/lib/cmake/example.cmake').read_text(),
                         'set(prefix "' + str(new) + '")\n')
        self.assertIn(str(new), (work / 'libxc-build/environment.sh').read_text())
        self.assertNotIn(str(self.prefix), (work / 'libxc-build/environment.sh').read_text())
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            cache.unpack(self.bundle, new, work, self.spec)

    def test_missing_acceptance_corruption_and_abi_changes_cannot_restore(self):
        self.pack()
        new, work = self.destination()
        with self.assertRaises(FileNotFoundError):
            cache.unpack(self.bundle, new, work, self.spec)
        self.assertEqual(list(new.iterdir()), [])
        self.accept()
        for field, value in (('target','4v100-avx512'), ('native_flags','znver5'), ('provider','c'*64)):
            changed = dict(self.spec, **{field:value})
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'identity'):
                cache.unpack(self.bundle, new, work, changed)
        with (self.bundle / 'payload.tar.gz').open('ab') as stream:
            stream.write(b'corruption')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            cache.unpack(self.bundle, new, work, self.spec)
        self.assertEqual(list(new.iterdir()), [])

    def test_unknown_binary_prefix_and_external_links_are_not_silently_patched(self):
        lib = self.prefix / 'dependencies/dbcsr/lib/libexample.a'
        lib.write_bytes(b'!<arch>\n' + str(self.prefix).encode())
        with self.assertRaisesRegex(ValueError, 'Nonrelocatable'):
            self.pack()
        shutil.rmtree(self.bundle)
        lib.write_bytes(b'valid static objects')
        (lib.parent / 'bad.so').symlink_to('/usr/lib/not-a-private-dependency')
        with self.assertRaisesRegex(ValueError, 'escapes'):
            self.pack()

    def test_duplicate_archive_entries_are_rejected_even_with_updated_archive_hash(self):
        record = self.pack()
        archive = self.bundle / 'payload.tar.gz'
        with tarfile.open(archive) as source:
            items = [(member, source.extractfile(member).read() if member.isfile() else None)
                     for member in source]
        with tarfile.open(archive, 'w:gz') as target:
            for member, data in items + [items[0]]:
                target.addfile(member, io.BytesIO(data) if data is not None else None)
        record.update(archive_sha256=cache.checksum(archive), archive_bytes=archive.stat().st_size)
        cache.write(self.bundle / 'manifest.json', record)
        self.accept()
        new, work = self.destination()
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            cache.unpack(self.bundle, new, work, self.spec)
        self.assertEqual(list(new.iterdir()), [])

    def test_application_source_change_keeps_key_but_dependency_patch_invalidates_it(self):
        source = self.root / 'source'
        base = source / 'tools/toolchain/scripts'
        for name in ('stage3/install_libxc.sh','stage4/install_libxs.sh',
                     'stage8/install_tblite.sh','stage8/install_dftd4.sh'):
            p = base / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('version="fixed"\n')
        (source / 'CMakeLists.txt').write_text('project(CP2K)\nfind_package(wignernj 0.8 REQUIRED)\n')
        before = cache.source_contract(source)
        (source / 'new-core-code.F').write_text('a new CP2K implementation\n')
        (source / 'CMakeLists.txt').write_text('# new CP2K commit\nproject(CP2K)\nfind_package(wignernj 0.8 REQUIRED)\n')
        self.assertEqual(before, cache.source_contract(source))
        (base / 'stage4/libxs-1.0.0-jit-handle.patch').write_text('a changed library repair\n')
        self.assertNotEqual(before, cache.source_contract(source))

    def test_cache_publication_requires_accepted_science_and_is_immutable(self):
        self.pack()
        project = self.root / 'project'
        run = project / 'runs/build-one'
        (run / 'results').mkdir(parents=True)
        shutil.copytree(self.bundle, run / 'auxiliary-cache')
        state = dict(enabled=True, hit=False, prefix=str(self.prefix), contract=self.spec,
                     candidate_manifest_sha256=cache.checksum(self.bundle / 'manifest.json'),
                     cache_archive_sha256=cache.read(self.bundle / 'manifest.json')['archive_sha256'])
        cache.write(run / 'results/auxiliary-cache.json', state)
        artifact = run / 'artifact.sif'
        manifest = dict(published=False, verified=False, source_sha='b'*40, sha256='c'*64,
                        identity=dict(install_prefix=str(self.prefix)), cp2k_science={})
        cache.write(artifact.with_suffix('.json'), manifest)
        with self.assertRaisesRegex(ValueError, 'unaccepted'):
            cache.promote(project, run, artifact)
        self.assertFalse((project / 'cache').exists())
        manifest.update(published=True, verified=True,
                        cp2k_science=dict(verified=True,artifact_sha256='c'*64,evidence_sha256='d'*64))
        cache.write(artifact.with_suffix('.json'), manifest)
        cache.promote(project, run, artifact)
        target = project / 'cache/cp2k-auxiliary' / cache.digest(self.spec)
        before = {p.name:cache.checksum(p) for p in target.iterdir()}
        cache.promote(project, run, artifact)
        self.assertEqual(before, {p.name:cache.checksum(p) for p in target.iterdir()})

    @unittest.skipUnless(shutil.which('cmake') and shutil.which('cc'), 'native CMake toolchain required')
    def test_real_static_and_shared_libraries_link_after_original_prefix_is_removed(self):
        source = self.root / 'native-source'
        source.mkdir()
        (source / 'answer.c').write_text('int answer(void) { return 42; }\n')
        (source / 'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.16)
project(cache_fixture C)
add_library(answer STATIC answer.c)
add_library(answer_shared SHARED answer.c)
set_target_properties(answer_shared PROPERTIES SOVERSION 1 INSTALL_RPATH "$ORIGIN")
install(TARGETS answer answer_shared EXPORT fixture DESTINATION lib)
install(EXPORT fixture DESTINATION lib/cmake/fixture FILE fixture-config.cmake)
''')
        install = self.prefix / 'dependencies/dbcsr'
        def run(*argv):
            return subprocess.run(argv, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        run('cmake','-S',str(source),'-B',str(self.root/'build'),'-DCMAKE_INSTALL_PREFIX='+str(install))
        run('cmake','--build',str(self.root/'build'))
        run('cmake','--install',str(self.root/'build'))
        self.pack()
        self.accept()
        new, work = self.destination()
        cache.unpack(self.bundle,new,work,self.spec)
        shutil.rmtree(self.prefix)
        consumer = self.root / 'consumer'
        consumer.mkdir()
        (consumer/'main.c').write_text('int answer(void); int main(void) {return answer()!=42;}\n')
        (consumer/'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.16)
project(cache_consumer C)
find_package(fixture CONFIG REQUIRED)
add_executable(static_consumer main.c)
target_link_libraries(static_consumer answer)
add_executable(shared_consumer main.c)
target_link_libraries(shared_consumer answer_shared)
''')
        output = self.root / 'consumer-build'
        run('cmake','-S',str(consumer),'-B',str(output),'-DCMAKE_PREFIX_PATH='+str(new/'dependencies/dbcsr'))
        run('cmake','--build',str(output))
        run(str(output/'static_consumer'))
        run(str(output/'shared_consumer'))

    @unittest.skipUnless(shutil.which('gfortran') and shutil.which('ar'), 'native Fortran toolchain required')
    def test_installed_fortran_source_debug_paths_do_not_bind_archive_to_old_prefix(self):
        # DBCSR compiles LibXS's installed Fortran source with debug info. This
        # reproduces the actual source-location dependency missed by C fixtures.
        source = self.prefix / 'dependencies/libxs/include/answer.f90'
        source.write_text('''module answer_module
contains
integer function answer()
answer = 42
end function
end module
''')
        module_dir = self.workspace / 'fortran-modules'
        module_dir.mkdir()
        obj = self.workspace / 'answer.o'
        archive = self.prefix / 'dependencies/dbcsr/lib/libanswer.a'
        def compile_source(*flags):
            subprocess.run(['gfortran', '-O3', '-g', *flags, '-J' + str(module_dir),
                            '-c', str(source), '-o', str(obj)], check=True, capture_output=True)
            subprocess.run(['ar', 'rcs', str(archive), str(obj)], check=True, capture_output=True)
            shutil.copyfile(module_dir / 'answer_module.mod',
                            self.prefix / 'dependencies/dbcsr/include/answer_module.mod')
        compile_source()
        self.assertIn(str(self.prefix).encode(), archive.read_bytes())
        with self.assertRaisesRegex(ValueError, 'Nonrelocatable'):
            self.pack()
        shutil.rmtree(self.bundle)
        compile_source('-ffile-prefix-map=' + str(self.prefix) + '=/sai-cp2k-install')
        original = archive.read_bytes()
        self.assertNotIn(str(self.prefix).encode(), original)
        self.pack()
        self.accept()
        new, work = self.destination()
        cache.unpack(self.bundle, new, work, self.spec)
        restored = new / 'dependencies/dbcsr/lib/libanswer.a'
        self.assertEqual(restored.read_bytes(), original)
        shutil.rmtree(self.prefix)
        consumer = self.workspace / 'main.f90'
        consumer.write_text('program main\nuse answer_module\nif (answer() /= 42) stop 1\nend program\n')
        executable = self.workspace / 'fortran-consumer'
        subprocess.run(['gfortran', '-I' + str(new / 'dependencies/dbcsr/include'),
                        str(consumer), str(restored), '-o', str(executable)], check=True, capture_output=True)
        subprocess.run([str(executable)], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()
