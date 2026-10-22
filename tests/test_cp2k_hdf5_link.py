"""Exercise CP2K's actual HDF5 options against a static imported-target chain.

The tiny libraries use C for all symbols so the test needs no HDF5 or Fortran
compiler. They model HDF5 1.14's separate Fortran-facing, C-stub and C-core
archives plus external ZLIB; this checks discovery/link propagation, not HDF5's
Fortran ABI or IO.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CP2KHDF5LinkTests(unittest.TestCase):
    def command(self, *arguments, check=True):
        result = subprocess.run(arguments, cwd=self.root, env=self.environment,
                                text=True, capture_output=True, timeout=60)
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def setUp(self):
        for tool in ('cmake', 'cc', 'ar', 'make'):
            if not shutil.which(tool):
                self.skipTest('real static linkage test requires ' + tool)
        work = ROOT / '.test-work'
        work.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='cp2k-hdf5-', dir=work)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        scratch = self.root / 'scratch'
        scratch.mkdir()
        self.environment = dict(os.environ, TMPDIR=str(scratch))
        self.prefix = self.root / 'private-hdf5'
        (self.prefix / 'lib').mkdir(parents=True)
        (self.prefix / 'include').mkdir()
        sources = {
            'hdf5': 'extern int fixture_zlib(void); int H5open(void) { return fixture_zlib(); }\n',
            'hdf5_f90cstub': 'extern int H5open(void); int h5open_c(void) { return H5open(); }\n',
            'hdf5_fortran': 'extern int h5open_c(void); int h5open_f(void) { return h5open_c(); }\n',
        }
        config = ['set(HDF5_FOUND TRUE)', 'set(HDF5_VERSION 1.14.6)',
                  'set(HDF5_ENABLE_PARALLEL TRUE)',
                  'set(HDF5_INCLUDE_DIR "${CMAKE_CURRENT_LIST_DIR}/include")']
        for name, source in sources.items():
            path = self.root / (name + '.c')
            path.write_text(source)
            object_file = path.with_suffix('.o')
            archive = self.prefix / 'lib' / ('lib' + name + '.a')
            self.command('cc', '-fPIC', '-c', str(path), '-o', str(object_file))
            self.command('ar', 'rcs', str(archive), str(object_file))
            target = 'hdf5::' + name + '-static'
            config += [f'add_library({target} STATIC IMPORTED)',
                       f'set_target_properties({target} PROPERTIES',
                       '  IMPORTED_CONFIGURATIONS RELEASE',
                       f'  IMPORTED_LOCATION_RELEASE "${{CMAKE_CURRENT_LIST_DIR}}/lib/lib{name}.a"',
                       '  INTERFACE_INCLUDE_DIRECTORIES "${CMAKE_CURRENT_LIST_DIR}/include")']
        self.zlib_root = self.root / 'receipt-zlib'
        (self.zlib_root / 'lib').mkdir(parents=True)
        (self.zlib_root / 'include').mkdir()
        (self.zlib_root / 'include/zlib.h').write_text('#define ZLIB_VERSION "1.3"\n')
        zlib_source = self.root / 'zlib.c'
        zlib_source.write_text('int fixture_zlib(void) { return 42; }\n')
        self.command('cc', '-fPIC', '-c', str(zlib_source), '-o', str(self.root / 'zlib.o'))
        self.command('ar', 'rcs', str(self.zlib_root / 'lib/libz.a'), str(self.root / 'zlib.o'))
        config += ['set_property(TARGET hdf5::hdf5-static PROPERTY',
                   '  INTERFACE_LINK_LIBRARIES ZLIB::ZLIB)',
                   'set_property(TARGET hdf5::hdf5_fortran-static PROPERTY',
                   '  INTERFACE_LINK_LIBRARIES hdf5::hdf5_f90cstub-static)',
                   'set_property(TARGET hdf5::hdf5_f90cstub-static PROPERTY',
                   '  INTERFACE_LINK_LIBRARIES hdf5::hdf5-static)']
        (self.prefix / 'hdf5-config.cmake').write_text('\n'.join(config) + '\n')
        (self.root / 'cp2k.c').write_text(
            'extern int h5open_f(void); int cp2k_probe(void) { return h5open_f(); }\n')
        (self.root / 'main.c').write_text(
            'extern int cp2k_probe(void); int main(void) { return cp2k_probe() == 42 ? 0 : 1; }\n')
        (self.root / 'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.20)
project(cp2k C)
find_package(HDF5 REQUIRED COMPONENTS C Fortran)
if(NOT HDF5_LIBRARIES STREQUAL "hdf5::hdf5-static;hdf5::hdf5_fortran-static")
  message(FATAL_ERROR "HDF5 native static targets were lost: ${HDF5_LIBRARIES}")
endif()
get_target_property(stub hdf5::hdf5_fortran-static INTERFACE_LINK_LIBRARIES)
get_target_property(core hdf5::hdf5_f90cstub-static INTERFACE_LINK_LIBRARIES)
if(NOT stub STREQUAL "hdf5::hdf5_f90cstub-static" OR NOT core STREQUAL "hdf5::hdf5-static")
  message(FATAL_ERROR "HDF5 static Fortran closure changed")
endif()
if(DROP_FORTRAN_STUB_EDGE)
  set_property(TARGET hdf5::hdf5_fortran-static PROPERTY INTERFACE_LINK_LIBRARIES "")
endif()
add_library(cp2k_fixture SHARED cp2k.c)
# Match the two HDF5 targets consumed by CP2K v2026.2.
target_link_libraries(cp2k_fixture PRIVATE HDF5::HDF5 hdf5::hdf5_fortran)
add_executable(consumer main.c)
target_link_libraries(consumer PRIVATE cp2k_fixture)
''')

    def production_options(self):
        script = (ROOT / 'controller/cp2k_build.sh').read_text()
        start = script.index('cmake_args=(\n')
        end = script.index('\n)', start) + 2
        # Evaluate the production argument array without running the build.
        # Other dependencies stay unset: only its HDF5 options enter this fixture.
        shell = ('cmake_compilers=()\nhdf5_root=$1\n' + script[start:end] +
                 '\nprintf "%s\\0" "${cmake_args[@]}"')
        result = self.command('bash', '-e', '-c', shell, 'cp2k-hdf5-options', str(self.prefix))
        options = []
        for argument in result.stdout.split('\0'):
            if argument.startswith('-DHDF5_'):
                options.append(argument)
            elif argument.startswith('-DZLIB_ROOT='):
                # Substitute only the fixture's location for the receipt prefix.
                options.append('-DZLIB_ROOT=' + str(self.zlib_root))
            elif argument.startswith('-DCMAKE_PROJECT_cp2k_INCLUDE='):
                path = Path(argument.split('=', 1)[1])
                self.assertEqual(path.parent, Path('/control'))
                options.append('-DCMAKE_PROJECT_cp2k_INCLUDE=' + str(ROOT / 'controller' / path.name))
        return options

    def configure(self, name, *extra, options=None, check=True):
        build = self.root / name
        result = self.command('cmake', '-G', 'Unix Makefiles', '-S', str(self.root), '-B', str(build),
                              '-DCMAKE_BUILD_TYPE=Release',
                              *(self.production_options() if options is None else options),
                              *extra, check=check)
        return build, result

    def test_production_options_link_and_execute_the_complete_static_closure(self):
        build, _ = self.configure('fixed')
        self.command('cmake', '--build', str(build), '--parallel', '2')
        self.command(str(build / 'consumer'))
        # A direct Fortran archive alone cannot stand in for its exported target.
        broken, _ = self.configure('missing-stub', '-DDROP_FORTRAN_STUB_EDGE=ON')
        result = self.command('cmake', '--build', str(broken), '--parallel', '2', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('h5open_c', result.stdout + result.stderr)

    def test_missing_project_hook_rejects_undefined_external_zlib_target(self):
        options = [argument for argument in self.production_options()
                   if not argument.startswith('-DCMAKE_PROJECT_cp2k_INCLUDE=')]
        _, result = self.configure('missing-zlib-target', options=options, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ZLIB::ZLIB', result.stdout + result.stderr)

    def test_default_shared_discovery_does_not_accept_the_static_only_fixture(self):
        options = [argument for argument in self.production_options()
                   if not argument.startswith('-DHDF5_USE_STATIC_LIBRARIES=')]
        _, result = self.configure('old-default', options=options, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('HDF5', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
