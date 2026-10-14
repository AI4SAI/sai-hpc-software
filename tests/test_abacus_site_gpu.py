"""Real local CMake/ELF fixtures for the Conda NCCL shadowing regression."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'controller/abacus_site_gpu.sh'
SYMBOLS = ('ncclPutSignal', 'ncclWaitSignal', 'ncclCommQueryProperties')


@unittest.skipUnless(shutil.which('cc') and shutil.which('nm'), 'C compiler and nm required')
class SiteGPUTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.nccl = self.root / 'site-nccl'
        self.cuda = self.root / 'site-cuda'
        self.ml = self.root / 'conda-ml'
        for prefix in (self.nccl, self.cuda, self.ml):
            for directory in ('include', 'lib', 'lib64', 'bin'):
                (prefix / directory).mkdir(parents=True)
        for prefix, symbols in ((self.nccl, SYMBOLS), (self.ml, ('ncclOldAPI',))):
            (prefix / 'include/nccl.h').write_text('/* discovery fixture */\n')
            source = '\n'.join(f'int {s}(void) {{ return 0; }}' for s in symbols)
            subprocess.run(['cc', '-x', 'c', '-shared', '-fPIC', '-Wl,-soname,libnccl.so.2',
                            '-o', str(prefix / 'lib/libnccl.so'), '-'], input=source,
                           check=True, capture_output=True, text=True)
        for prefix in (self.cuda, self.ml):
            (prefix / 'include/cuda.h').write_text('/* discovery fixture */\n')
            (prefix / 'lib64/libcudart.so').write_text('discovery fixture\n')
            (prefix / 'bin/nvcc').write_text('#!/bin/sh\nexit 0\n')
            (prefix / 'bin/nvcc').chmod(0o755)
        self.env = dict(os.environ, NCCL_ROOT=str(self.nccl), CUDA_HOME=str(self.cuda),
                        CMAKE_PREFIX_PATH=str(self.ml))

    def shell(self, command, check=True):
        return subprocess.run(['bash', '-euc', 'source "$1"; ' + command,
                               'gpu-test', str(SCRIPT)], env=self.env, text=True,
                              capture_output=True, check=check)

    def test_site_symbols_and_paths_are_required(self):
        result = self.shell('printf "%s\\n" "$CMAKE_PREFIX_PATH" "${abacus_gpu_options[@]}"')
        self.assertEqual(result.stdout.splitlines()[0], f'{self.nccl}:{self.cuda}:{self.ml}')
        self.env['NCCL_ROOT'] = str(self.ml)
        bad = self.shell('echo SHOULD_NOT_RUN', check=False)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn('Site NCCL lacks cuBLASMp symbol: ncclPutSignal', bad.stderr)
        self.env['NCCL_ROOT'] = str(self.nccl)
        (self.cuda / 'bin/nvcc').unlink()
        self.assertNotEqual(self.shell('echo SHOULD_NOT_RUN', check=False).returncode, 0)

    def test_cache_rejects_replaced_selection_and_transitive_conda_cuda(self):
        options = self.shell('printf "%s\\n" "${abacus_gpu_options[@]}"').stdout.splitlines()
        good = '\n'.join(row[2:] for row in options) + '\n'
        cache = self.root / 'CMakeCache.txt'
        self.env['TEST_CACHE'] = str(cache)
        cache.write_text(good)
        self.assertIn('SITE_GPU_CONFIG_VERIFIED', self.shell('abacus_check_site_gpu "$TEST_CACHE"').stdout)
        cache.write_text(good.replace('CMAKE_CUDA_COMPILER:FILEPATH=', 'CMAKE_CUDA_COMPILER:STRING='))
        self.assertIn('SITE_GPU_CONFIG_VERIFIED', self.shell('abacus_check_site_gpu "$TEST_CACHE"').stdout)
        for bad in (good.replace(str(self.nccl), str(self.ml)),
                    good.replace('CMAKE_CUDA_COMPILER:FILEPATH=', 'CMAKE_CUDA_COMPILER:UNINITIALIZED='),
                    good.replace(f'CMAKE_CUDA_COMPILER:FILEPATH={self.cuda}', f'CMAKE_CUDA_COMPILER:STRING={self.ml}'),
                    good + 'CUDA_cublas_LIBRARY:FILEPATH=/opt/apps/conda_env/test/lib/libcublas.so\n'):
            cache.write_text(bad)
            self.assertNotEqual(self.shell('abacus_check_site_gpu "$TEST_CACHE"', check=False).returncode, 0)

    def test_real_link_requires_the_three_missing_symbols(self):
        source = '\n'.join(f'extern int {s}(void);' for s in SYMBOLS)
        source += '\nint main(void) { return ' + ' + '.join(s + '()' for s in SYMBOLS) + '; }\n'
        for prefix, expected in ((self.ml, False), (self.nccl, True)):
            result = subprocess.run(['cc', '-x', 'c', '-', '-x', 'none',
                                     str(prefix / 'lib/libnccl.so'), '-o', str(self.root / 'probe')],
                                    input=source, text=True, capture_output=True)
            self.assertEqual(result.returncode == 0, expected, result.stderr)
            if not expected:
                for symbol in SYMBOLS:
                    self.assertIn(symbol, result.stderr)

    @unittest.skipUnless(shutil.which('cmake'), 'CMake required for discovery regression')
    def test_cmake_prefix_search_overrides_hints_but_explicit_site_options_win(self):
        source = self.root / 'source'
        source.mkdir()
        (source / 'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.18)
project(GPUDiscovery NONE)
find_library(NCCL_LIBRARY NAMES nccl HINTS "$ENV{NCCL_ROOT}" PATH_SUFFIXES lib REQUIRED)
find_path(NCCL_INCLUDE_DIR nccl.h HINTS "$ENV{NCCL_ROOT}" PATH_SUFFIXES include REQUIRED)
find_program(CMAKE_CUDA_COMPILER NAMES nvcc HINTS "$ENV{CUDA_HOME}" PATH_SUFFIXES bin REQUIRED)
''')
        old = self.root / 'old'
        subprocess.run(['cmake', '-S', str(source), '-B', str(old)], env=self.env,
                       check=True, capture_output=True, text=True)
        old_cache = (old / 'CMakeCache.txt').read_text()
        self.assertIn(f'NCCL_LIBRARY:FILEPATH={self.ml}/lib/libnccl.so', old_cache)
        self.assertIn(f'CMAKE_CUDA_COMPILER:FILEPATH={self.ml}/bin/nvcc', old_cache)
        self.env.update(TEST_SOURCE=str(source), TEST_BUILD=str(self.root / 'fixed'))
        self.shell('cmake -S "$TEST_SOURCE" -B "$TEST_BUILD" "${abacus_gpu_options[@]}"; '
                   'abacus_check_site_gpu "$TEST_BUILD/CMakeCache.txt"')

    @unittest.skipUnless(shutil.which('cmake'), 'CMake required for compiler-path regression')
    def test_real_cmake_compiler_path_normalizes_file_to_string(self):
        source = self.root / 'compiler-path'
        source.mkdir()
        (source / 'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.18)
project(CompilerPath NONE)
include(CMakeDetermineCompiler)
_cmake_find_compiler_path(CUDA)
''')
        self.env.update(TEST_SOURCE=str(source), TEST_BUILD=str(self.root / 'compiler-build'))
        self.shell('cmake -S "$TEST_SOURCE" -B "$TEST_BUILD" "${abacus_gpu_options[@]}"; '
                   'abacus_check_site_gpu "$TEST_BUILD/CMakeCache.txt"')
        cache = (self.root / 'compiler-build/CMakeCache.txt').read_text()
        self.assertIn(f'CMAKE_CUDA_COMPILER:STRING={self.cuda}/bin/nvcc', cache)

    def test_policy_is_hashed_uploaded_and_checked_before_compilation(self):
        for name in ('software_controller.py', 'ci.py'):
            self.assertIn('"abacus_site_gpu.sh"', (ROOT / 'controller' / name).read_text())
        full = (ROOT / 'controller/abacus_full_dependencies.sh').read_text()
        self.assertLess(full.index('source /control/abacus_site_gpu.sh'), full.index('full_build_install test-drive'))
        self.assertIn('"${abacus_gpu_options[@]}"', full)
        build = (ROOT / 'controller/abacus_build.sh').read_text()
        self.assertLess(build.index('abacus_check_site_gpu'), build.index('cmake --build /workspace/build'))


if __name__ == '__main__':
    unittest.main()
