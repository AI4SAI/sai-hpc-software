"""Regress CMake prefix precedence separately from real cluster MPI acceptance."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'controller/abacus_site_mpi.sh'


class SiteMPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mpi = self.root / 'site-mpi'
        self.ml = self.root / 'conda-ml'
        self.site = self.root / 'site-math'
        self.full = self.root / 'full'
        for prefix in (self.mpi, self.ml):
            for directory in ('bin', 'lib', 'include'):
                (prefix / directory).mkdir(parents=True)
            for name in ('mpicc', 'mpicxx', 'mpifort', 'mpiexec'):
                wrapper = prefix / 'bin' / name
                wrapper.write_text('#!/bin/sh\nexit 0\n')
                wrapper.chmod(0o755)
            for name in ('mpi', 'mpi_mpifh', 'mpi_usempi_ignore_tkr', 'mpi_usempif08'):
                (prefix / 'lib' / f'lib{name}.so').write_text('discovery fixture, not an ELF\n')
            for name in ('mpi.h', 'mpif.h'):
                (prefix / 'include' / name).write_text('discovery fixture\n')
            (prefix / 'lib/mpi.mod').write_text('discovery fixture\n')
        self.env = dict(os.environ, MPI_HOME=str(self.mpi), full_deps=str(self.full),
                        ml_root=str(self.ml), CMAKE_PREFIX_PATH=str(self.site))

    def shell(self, command, check=True):
        return subprocess.run(['bash', '-euc', 'source "$1"; ' + command,
                               'mpi-test', str(SCRIPT)], env=self.env, text=True,
                              capture_output=True, check=check)

    def test_site_prefix_and_every_mpi_selector_precede_conda(self):
        result = self.shell('printf "%s\\n" "$CMAKE_PREFIX_PATH" "${abacus_mpi_options[@]}"')
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], ':'.join(map(str, (self.full, self.mpi, self.site, self.ml))))
        self.assertEqual(len(lines[1:]), 13)
        for option in lines[1:]:
            self.assertTrue(Path(option.split('=', 1)[1]).is_relative_to(self.mpi), option)
        self.assertIn(f'-DMPI_Fortran_MODULE_DIR:PATH={self.mpi}/lib', lines)
        self.assertNotIn('_WORKS', result.stdout)
        self.env['CMAKE_PREFIX_PATH'] = ''
        self.assertEqual(self.shell('printf "%s" "$CMAKE_PREFIX_PATH"').stdout,
                         ':'.join(map(str, (self.full, self.mpi, self.ml))))

    def test_missing_site_component_stops_without_falling_back(self):
        (self.mpi / 'bin/mpifort').unlink()
        self.assertNotEqual(self.shell('echo SHOULD_NOT_RUN', check=False).returncode, 0)

    def test_recipe_hash_upload_and_precompile_gate_include_the_mpi_policy(self):
        for name in ('software_controller.py', 'ci.py'):
            self.assertIn('"abacus_site_mpi.sh"', (ROOT / 'controller' / name).read_text())
        full = (ROOT / 'controller/abacus_full_dependencies.sh').read_text()
        self.assertIn('source /control/abacus_site_mpi.sh', full)
        self.assertIn('abacus_ml_options=("${abacus_mpi_options[@]}"', full)
        build = (ROOT / 'controller/abacus_build.sh').read_text()
        self.assertLess(build.index('abacus_check_site_mpi /workspace/build/CMakeCache.txt'),
                        build.index('cmake --build /workspace/build'))

    def test_cache_gate_rejects_shadowed_library_or_failed_language_probe(self):
        options = self.shell('printf "%s\\n" "${abacus_mpi_options[@]}"').stdout.splitlines()
        good = '\n'.join(option[2:] for option in options) + '\n'
        good += '\n'.join(f'MPI_RESULT_{lang}_test_mpi_normal:INTERNAL=TRUE'
                          for lang in ('C', 'CXX', 'Fortran')) + '\n'
        cache = self.root / 'CMakeCache.txt'
        self.env['TEST_CACHE'] = str(cache)
        cache.write_text(good)
        self.assertIn('SITE_MPI_CONFIG_VERIFIED',
                      self.shell('abacus_check_site_mpi "$TEST_CACHE"').stdout)
        for bad in (good.replace(f'={self.mpi}/lib/libmpi.so', f'={self.ml}/lib/libmpi.so'),
                    good.replace('Fortran_test_mpi_normal:INTERNAL=TRUE',
                                 'Fortran_test_mpi_normal:INTERNAL=FALSE')):
            cache.write_text(bad)
            result = self.shell('abacus_check_site_mpi "$TEST_CACHE"', check=False)
            self.assertNotEqual(result.returncode, 0)

    @unittest.skipUnless(shutil.which('cmake'), 'CMake required for real discovery regression')
    def test_real_cmake_reproduces_old_shadowing_and_resolves_repaired_prefixes(self):
        source = self.root / 'source'
        source.mkdir()
        (self.site / 'lib').mkdir(parents=True)
        (self.site / 'lib/libsite_math.so').write_text('site fixture\n')
        (self.ml / 'lib/libsite_math.so').write_text('conda fixture\n')
        package = self.ml / 'lib/cmake/MLFixture'
        package.mkdir(parents=True)
        (package / 'MLFixtureConfig.cmake').write_text('set(MLFixture_FOUND TRUE)\n')
        (source / 'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.18)
project(PrefixDiscovery NONE)
find_program(MPIEXEC_EXECUTABLE NAMES mpiexec HINTS "$ENV{MPI_HOME}" PATH_SUFFIXES bin REQUIRED)
find_library(MPI_mpi_LIBRARY NAMES mpi HINTS "$ENV{MPI_HOME}/lib" REQUIRED)
find_path(MPI_C_HEADER_DIR mpi.h HINTS "$ENV{MPI_HOME}/include" REQUIRED)
find_library(SITE_MATH_LIBRARY NAMES site_math REQUIRED)
find_package(MLFixture CONFIG REQUIRED)
''')
        old = self.root / 'old'
        env = dict(self.env, CMAKE_PREFIX_PATH=f'{self.ml}:{self.site}')
        subprocess.run(['cmake', '-S', str(source), '-B', str(old)], env=env,
                       check=True, capture_output=True, text=True)
        old_cache = (old / 'CMakeCache.txt').read_text()
        self.assertIn(f'MPI_mpi_LIBRARY:FILEPATH={self.ml}/lib/libmpi.so', old_cache)
        self.assertIn(f'MPIEXEC_EXECUTABLE:FILEPATH={self.ml}/bin/mpiexec', old_cache)
        self.env.update(TEST_SOURCE=str(source), TEST_BUILD=str(self.root / 'fixed'))
        self.shell('cmake -S "$TEST_SOURCE" -B "$TEST_BUILD" "${abacus_mpi_options[@]}"')
        new_cache = (self.root / 'fixed/CMakeCache.txt').read_text()
        self.assertIn(f'MPI_mpi_LIBRARY:FILEPATH={self.mpi}/lib/libmpi.so', new_cache)
        self.assertIn(f'MPIEXEC_EXECUTABLE:FILEPATH={self.mpi}/bin/mpiexec', new_cache)
        self.assertIn(f'SITE_MATH_LIBRARY:FILEPATH={self.site}/lib/libsite_math.so', new_cache)
        self.assertIn(f'MLFixture_DIR:PATH={package}', new_cache)


if __name__ == '__main__':
    unittest.main()
