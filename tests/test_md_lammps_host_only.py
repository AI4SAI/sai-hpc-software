import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'controller'))
import md_lammps_host_only as host
from md_registration import integrate as register_deepmd


class PatchTests(unittest.TestCase):
    def test_idempotent_with_normal_deepmd_registration(self):
        before = ('set(LAMMPS_STYLE_HEADERS_DIR ${CMAKE_CURRENT_BINARY_DIR}/styles)\n'
                  'GenerateStyleSources(${LAMMPS_STYLE_HEADERS_DIR})\n' + host.ANCHOR)
        registered = register_deepmd(before)
        after, status = host.integrate(registered, '--host-only)')
        self.assertEqual(status, 'applied')
        self.assertEqual(host.integrate(after, '--host-only)'), (after, 'already-present'))
        self.assertEqual(register_deepmd(after), after)
        self.assertLess(after.index('source/lmp/builtin.cmake'), after.index('get_target_property'))

    def test_unrecognized_sources_and_older_wrappers_keep_the_original_build(self):
        for text, wrapper, status in (
            ('new CMake layout\n', '--host-only)', 'skipped-unrecognized-cmake'),
            (host.ANCHOR * 2, '--host-only)', 'skipped-unrecognized-cmake'),
            (host.ANCHOR, 'older wrapper', 'skipped-unsupported-wrapper'),
        ):
            with self.subTest(status=status):
                self.assertEqual(host.integrate(text, wrapper), (text, status))

    def test_new_upstream_rules_are_not_replaced_by_our_older_backport(self):
        upstream = host.ANCHOR + 'set_source_files_properties(new.cpp PROPERTIES COMPILE_OPTIONS "--host-only")\n'
        self.assertEqual(host.integrate(upstream, '--host-only)'), (upstream, 'upstream-managed'))


@unittest.skipUnless(shutil.which('cmake') and shutil.which('g++'), 'requires CMake and a C++ compiler')
class CMakeSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source'
        self.build = self.root / 'build'
        self.receipt = self.root / 'host-only.json'
        self.files = {
            'src/plain.cpp': 'int plain() {return 1;}\n',
            'src/comment.cpp': '// #include "kokkos_type.h"\nint comment() {return 2;}\n',
            'src/KOKKOS/device.cpp': 'int device() {return 3;}\n',
            'src/uses_kokkos.cpp': '# include <Kokkos_Core.hpp>\n',
            'src/random.cpp': '#include "random_external_state.h"\n',
            'src/reaxff.cpp': '#include "reaxff_types.h"\n',
            'lib/external.cpp': 'int external() {return 4;}\n',
        }
        for name, content in self.files.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        wrapper = self.source / 'lib/kokkos/bin/nvcc_wrapper'
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text('--host-only)\n')
        cmake = self.source / 'cmake/CMakeLists.txt'
        cmake.parent.mkdir()
        cmake.write_text('''cmake_minimum_required(VERSION 3.20)
project(selection LANGUAGES CXX)
option(PKG_KOKKOS "" ON)
option(Kokkos_ENABLE_CUDA "" ON)
set(LAMMPS_SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../src")
get_filename_component(LAMMPS_SOURCE_DIR "${LAMMPS_SOURCE_DIR}" ABSOLUTE)
set(LAMMPS_CXX_COMPILER_NAME "g++")
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/style.cpp" "int style() {return 5;}\\n")
set_property(GLOBAL PROPERTY LAMMPS_STYLE_SOURCES "${CMAKE_CURRENT_BINARY_DIR}/style.cpp")
add_library(lammps STATIC
''' + ''.join('  "' + str(self.source / name) + '"\n' for name in self.files) + ')\n' + host.ANCHOR)

    def configure(self, *options):
        prepared = host.apply(self.source, self.receipt)
        result = subprocess.run(['cmake', '-S', str(self.source / 'cmake'), '-B', str(self.build),
                                 '-DCMAKE_CXX_COMPILER=' + shutil.which('g++'),
                                 '-DCMAKE_EXPORT_COMPILE_COMMANDS=ON', *options],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        database = self.build / 'compile_commands.json'
        rows = json.loads(database.read_text())
        selected = {Path(row['file']).relative_to(self.source).as_posix() for row in rows
                    if '--host-only' in shlex.split(row['command'])}
        return prepared, database, rows, selected

    def test_actual_cmake_keeps_kokkos_indirect_headers_and_external_sources_on_their_original_path(self):
        prepared, database, rows, selected = self.configure()
        self.assertEqual(selected, {'src/plain.cpp', 'src/comment.cpp'})
        observed = host.verify(database, self.receipt)
        self.assertEqual(observed['host_only_commands'], 2)
        self.assertEqual(observed['compiler_commands'], len(self.files) + 1)
        self.assertNotEqual(prepared['before_sha256'], observed['after_sha256'])
        self.assertEqual(observed, json.loads(self.receipt.read_text()))

    def test_disabled_kokkos_or_cuda_keeps_all_original_commands(self):
        for option in ('-DPKG_KOKKOS=OFF', '-DKokkos_ENABLE_CUDA=OFF'):
            with self.subTest(option=option):
                _, _, _, selected = self.configure('-DPKG_KOKKOS=ON', '-DKokkos_ENABLE_CUDA=ON', option)
                self.assertFalse(selected)

    def test_missing_effective_flags_or_modified_cmake_cannot_claim_success(self):
        _, database, rows, _ = self.configure()
        for row in rows:
            row['command'] = row['command'].replace('--host-only', '')
        database.write_text(json.dumps(rows))
        with self.assertRaisesRegex(ValueError, 'selected no compiler commands'):
            host.verify(database, self.receipt)
        cmake = self.source / 'cmake/CMakeLists.txt'
        cmake.write_text(cmake.read_text() + '\n# altered after preparation\n')
        with self.assertRaisesRegex(ValueError, 'changed after host-only preparation'):
            host.verify(database, self.receipt)

    def test_kokkos_or_cuda_sources_cannot_be_misclassified_as_host_only(self):
        _, database, rows, _ = self.configure()
        for name in ('src/KOKKOS/device.cpp', 'src/device.cu'):
            with self.subTest(name=name):
                changed = rows + [dict(directory=str(self.build), file=str(self.source / name),
                                       arguments=['nvcc_wrapper', '--host-only', '-c', name])]
                database.write_text(json.dumps(changed))
                with self.assertRaisesRegex(ValueError, 'incorrectly selected'):
                    host.verify(database, self.receipt)


if __name__ == '__main__':
    unittest.main()
