"""Run CP2K's install-directory options against its CACHE PATH convention.

A real shared library, CMake export and pkg-config file model the upstream
install rules without compiling CP2K. The old untyped command-line option is
configured at /, but its escaping install plan is rejected before any install.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def contract_input(install_line):
    # Reuse the existing complete feature/compiler fixture; replace only the
    # install-directory entry under test, never pretend our tiny C library is
    # a full CP2K feature build.
    from test_cp2k_contract import CP2KContractTests, contract
    target = '4v100-avx512'
    prefix = '/opt/software/cp2k/test/' + target
    valid = CP2KContractTests().cache(target)
    contract.check_cache(valid, prefix, target)
    text = re.sub(r'^CMAKE_INSTALL_LIBDIR:[^\n]*', install_line, valid, flags=re.M)
    return contract, text, prefix, target


class CP2KInstallDirectoryContractTests(unittest.TestCase):
    def test_contract_rejects_absolute_parent_relative_and_missing_install_directory(self):
        for entry in ('CMAKE_INSTALL_LIBDIR:PATH=/lib64',
                      'CMAKE_INSTALL_LIBDIR:STRING=../lib64', ''):
            with self.subTest(entry=entry):
                contract, text, prefix, target = contract_input(entry)
                with self.assertRaisesRegex(ValueError, 'install library directory'):
                    contract.check_cache(text, prefix, target)


class CP2KInstallLayoutTests(unittest.TestCase):
    def command(self, *arguments, cwd=None):
        result = subprocess.run(arguments, cwd=cwd or self.driver, env=self.environment,
                                text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def setUp(self):
        for tool in ('cmake', 'cc', 'make', 'bash'):
            if not shutil.which(tool):
                self.skipTest('real CMake install test requires ' + tool)
        workspace = ROOT / '.test-work'
        workspace.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='cp2k-install-', dir=workspace)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.driver = self.root / 'configure cwd'
        self.source = self.root / 'source'
        self.prefix = self.root / 'installed prefix'
        scratch = self.root / 'scratch'
        for directory in (self.driver, self.source, scratch):
            directory.mkdir()
        self.environment = dict(os.environ, TMPDIR=str(scratch))
        # Do not inherit a caller's staging root or replace the real C compiler.
        self.environment.pop('DESTDIR', None)
        self.environment.pop('CMAKE_INSTALL_MODE', None)
        self.environment['CC'] = shutil.which('cc')
        (self.source / 'cp2k.c').write_text('int cp2k_fixture(void) { return 42; }\n')
        (self.source / 'cp2k.h').write_text('int cp2k_fixture(void);\n')
        (self.source / 'cp2kConfig.cmake').write_text(
            'include("${CMAKE_CURRENT_LIST_DIR}/cp2kTargets.cmake")\n')
        (self.source / 'cp2k.pc.in').write_text('''prefix=@CMAKE_INSTALL_PREFIX@
libdir=${prefix}/@CMAKE_INSTALL_LIBDIR@
includedir=${prefix}/include
Name: cp2k-install-fixture
Description: CP2K installation layout regression fixture
Version: 1.0.0
Libs: -L${libdir} -lcp2k_fixture
Cflags: -I${includedir}
''')
        (self.source / 'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.20)
project(cp2k C)
# CP2K v2026.2 root CMakeLists.txt declares this cache entry as PATH. An
# untyped -D value is made absolute relative to the configure working directory.
set(CMAKE_INSTALL_LIBDIR "lib" CACHE PATH "Library installation directory")
add_library(cp2k_fixture SHARED cp2k.c)
set_target_properties(cp2k_fixture PROPERTIES VERSION 1.0.0 SOVERSION 1)
target_include_directories(cp2k_fixture INTERFACE "$<INSTALL_INTERFACE:include>")
install(TARGETS cp2k_fixture EXPORT cp2kTargets
        LIBRARY DESTINATION "${CMAKE_INSTALL_LIBDIR}")
install(FILES cp2k.h DESTINATION include)
configure_file(cp2k.pc.in cp2k.pc @ONLY)
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/cp2k.pc"
        DESTINATION "${CMAKE_INSTALL_LIBDIR}/pkgconfig")
install(EXPORT cp2kTargets NAMESPACE CP2K::
        DESTINATION "${CMAKE_INSTALL_LIBDIR}/cmake/cp2k")
install(FILES cp2kConfig.cmake
        DESTINATION "${CMAKE_INSTALL_LIBDIR}/cmake/cp2k")
''')

    def production_options(self):
        script = (ROOT / 'controller/cp2k_build.sh').read_text()
        start = script.index('cmake_args=(\n')
        end = script.index('\n)', start) + 2
        # Evaluate the actual array, substituting only the installation prefix.
        # Unrelated compiler/dependency options do not enter the small fixture.
        shell = ('cmake_compilers=()\nprefix=$1\n' + script[start:end] +
                 '\nprintf "%s\\0" "${cmake_args[@]}"')
        output = self.command('bash', '-e', '-c', shell,
                              'cp2k-install-options', str(self.prefix)).stdout
        options = [argument for argument in output.split('\0')
                   if re.match(r'-DCMAKE_INSTALL_(?:PREFIX|LIBDIR)(?::[^=]+)?=', argument)]
        self.assertEqual(len(options), 2, options)
        return options

    def configure(self, name, options=None, cwd=None):
        build = self.root / name
        self.command('cmake', '-G', 'Unix Makefiles', '-S', str(self.source), '-B', str(build),
                     '-DCMAKE_BUILD_TYPE=Release',
                     *(self.production_options() if options is None else options), cwd=cwd)
        return build

    def install_destinations(self, build):
        """Reject escaped destinations before allowing a real install command."""
        script = (build / 'cmake_install.cmake').read_text()
        destinations = re.findall(r'file\(INSTALL DESTINATION "([^"]+)"', script)
        self.assertTrue(destinations, 'CMake did not generate installation rules')
        result = []
        for destination in destinations:
            path = Path(destination.replace('${CMAKE_INSTALL_PREFIX}', str(self.prefix)))
            if not path.is_absolute() or not path.resolve().is_relative_to(self.prefix.resolve()):
                raise ValueError('install destination escapes prefix: ' + str(path))
            result.append(path)
        return result

    def test_production_options_install_library_and_consumable_exports_inside_prefix(self):
        build = self.configure('build')
        entry = next(line for line in (build / 'CMakeCache.txt').read_text().splitlines()
                     if line.startswith('CMAKE_INSTALL_LIBDIR:'))
        contract, text, prefix, target = contract_input(entry)
        contract.check_cache(text, prefix, target)
        self.command('cmake', '--build', str(build), '--parallel', '2')
        self.install_destinations(build)
        self.command('cmake', '--install', str(build))
        installed = [Path(line) for line in (build / 'install_manifest.txt').read_text().splitlines()]
        self.assertTrue(installed)
        for path in installed:
            self.assertTrue(path.resolve().is_relative_to(self.prefix.resolve()), path)
            self.assertTrue(path.is_file(), path)
        expected = {
            'lib64/libcp2k_fixture.so', 'lib64/libcp2k_fixture.so.1',
            'lib64/libcp2k_fixture.so.1.0.0', 'include/cp2k.h',
            'lib64/pkgconfig/cp2k.pc', 'lib64/cmake/cp2k/cp2kConfig.cmake',
            'lib64/cmake/cp2k/cp2kTargets.cmake',
            'lib64/cmake/cp2k/cp2kTargets-release.cmake',
        }
        self.assertEqual({str(path.relative_to(self.prefix)) for path in installed}, expected)
        pkgconfig = (self.prefix / 'lib64/pkgconfig/cp2k.pc').read_text()
        self.assertIn('prefix=' + str(self.prefix) + '\n', pkgconfig)
        self.assertIn('libdir=${prefix}/lib64\n', pkgconfig)
        self.assertFalse((self.driver / 'lib64').exists())
        # Consume the installed export, so the test also exercises its library
        # location and include interface rather than only finding filenames.
        consumer = self.root / 'consumer'
        consumer.mkdir()
        (consumer / 'main.c').write_text(
            '#include <cp2k.h>\nint main(void) { return cp2k_fixture() == 42 ? 0 : 1; }\n')
        (consumer / 'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.20)
project(consumer C)
find_package(cp2k CONFIG REQUIRED)
add_executable(consumer main.c)
target_link_libraries(consumer PRIVATE CP2K::cp2k_fixture)
''')
        consumer_build = self.root / 'consumer-build'
        self.command('cmake', '-G', 'Unix Makefiles', '-S', str(consumer), '-B', str(consumer_build),
                     '-Dcp2k_DIR=' + str(self.prefix / 'lib64/cmake/cp2k'))
        self.command('cmake', '--build', str(consumer_build), '--parallel', '2')
        self.command(str(consumer_build / 'consumer'))

    def test_old_untyped_option_generates_escaping_install_rules_without_installing(self):
        options = [('-DCMAKE_INSTALL_LIBDIR=lib64' if re.match(
            r'-DCMAKE_INSTALL_LIBDIR(?::[^=]+)?=', option) else option)
            for option in self.production_options()]
        # Match the production container cwd. Configure/build output remains
        # under -B in our temporary directory; never run --install for this plan.
        build = self.configure('old-untyped-build', options=options, cwd='/')
        cache = (build / 'CMakeCache.txt').read_text()
        self.assertIn('CMAKE_INSTALL_LIBDIR:PATH=/lib64\n', cache)
        entry = next(line for line in cache.splitlines() if line.startswith('CMAKE_INSTALL_LIBDIR:'))
        contract, text, prefix, target = contract_input(entry)
        with self.assertRaisesRegex(ValueError, 'install library directory'):
            contract.check_cache(text, prefix, target)
        with self.assertRaisesRegex(ValueError, r'install destination escapes prefix: /lib64'):
            self.install_destinations(build)
        self.assertFalse((build / 'install_manifest.txt').exists())
        self.assertFalse(self.prefix.exists())


if __name__ == '__main__':
    unittest.main()
