import copy
import hashlib
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'controller'))
import md_lammps_cuda as cuda
import md_lammps_standalone as standalone


class CudaConfigurationTests(unittest.TestCase):
    def fixture(self, root, modern):
        source = root / 'GPU.cmake'
        source.write_text('enable_language(CUDA)\n' if modern else 'find_package(CUDA REQUIRED)\n')
        cache = ['FFT_KOKKOS:STRING=CUFFT', 'CUDA_MPS_SUPPORT:BOOL=ON',
                 'CMAKE_INSTALL_LIBDIR:PATH=lib', 'GPU_ARCH:STRING=' + ('70' if modern else 'sm_70')]
        if not modern:
            cache += ['CUDA_BUILD_MULTIARCH:BOOL=OFF', 'CUDPP_OPT:BOOL=OFF']
        (root / 'CMakeCache.txt').write_text('\n'.join(cache) + '\n')
        for target, flags in [('lammps', '-DFFT_KOKKOS_CUFFT'), ('gpu', '-DCUDA_MPS_SUPPORT'),
                              ('gpu_fatbin', '--generate-code=arch=compute_70,code=[compute_70,sm_70]')]:
            directory = root / 'CMakeFiles' / (target + '.dir')
            directory.mkdir(parents=True)
            (directory / 'flags.make').write_text(flags)
        return source

    def test_legacy_and_native_cuda_use_their_own_arch_and_host_compiler_options(self):
        for modern in (False, True):
            with self.subTest(modern=modern), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = self.fixture(root, modern)
                options = cuda.options(source, '/opt/gcc/bin/g++')
                self.assertIn('-DGPU_ARCH=' + ('70' if modern else 'sm_70'), options)
                self.assertIn(('-DCMAKE_CUDA_HOST_COMPILER=' if modern else '-DCUDA_HOST_COMPILER=') + '/opt/gcc/bin/g++', options)
                self.assertEqual(any('CUDPP_OPT' in arg for arg in options), not modern)
                self.assertEqual(cuda.verify(source, root), 'native-cuda' if modern else 'find-cuda')

    def test_inert_cache_options_or_missing_gpu_definitions_fail(self):
        for modern in (False, True):
            for before, after in [('CUFFT', 'KISS'), (':BOOL', ':UNINITIALIZED'),
                                  ('LIBDIR:PATH=lib', 'LIBDIR:PATH=lib64'), ('GPU_ARCH:STRING', 'GPU_ARCH:UNINITIALIZED')]:
                with self.subTest(modern=modern, before=before), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    source = self.fixture(root, modern)
                    cache = root / 'CMakeCache.txt'
                    cache.write_text(cache.read_text().replace(before, after))
                    with self.assertRaises(ValueError):
                        cuda.verify(source, root)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.fixture(root, True)
            (root / 'CMakeFiles/gpu_fatbin.dir/flags.make').write_text('arch=compute_80,code=sm_80')
            with self.assertRaisesRegex(ValueError, 'Volta'):
                cuda.verify(source, root)
            source.write_text('# unexpected future CUDA build mechanism\n')
            with self.assertRaisesRegex(ValueError, 'unrecognized'):
                cuda.options(source, '/opt/gcc/bin/g++')


class StandaloneTests(unittest.TestCase):
    def delivery(self, target='16v100-avx2'):
        return standalone.make_delivery(dict(track='development', source_ref='develop',
                                            source_sha='a' * 40, source_version='develop'), target)

    def test_lammps_identity_has_an_external_dependency_not_a_paired_source(self):
        for target in standalone.TARGETS:
            delivery = self.delivery(target)
            self.assertEqual(standalone.validate_delivery(delivery), delivery)
            self.assertEqual(delivery['identity']['stack_sources'], {})
            self.assertEqual(delivery['deepmd_dependency']['identity']['source_version'], 'v3.2.0')
            changed = copy.deepcopy(delivery)
            changed['deepmd_dependency']['identity']['source_sha'] = 'b' * 40
            with self.assertRaises(ValueError):
                standalone.validate_delivery(changed)

    def test_job_uses_readonly_deepmd_and_one_lammps_allocation(self):
        delivery = self.delivery()
        script = standalone.render(delivery, 'standalone-test')
        subprocess.run(['bash', '-n'], input=script, text=True, check=True)
        prefix = delivery['deepmd_dependency']['identity']['install_prefix']
        self.assertIn(prefix + ':' + prefix + ':ro', script)
        self.assertIn('#SBATCH --gpus-per-node=4', script)
        self.assertIn('BUILD_JOBS=24', script)
        self.assertIn('cp2k_image_driver.py', script)
        self.assertNotIn('build-deepmd', script)
        self.assertNotIn('afterok:', script)
        self.assertNotIn('--cpus-per-task=1', script)
        self.assertNotIn('TMPDIR=/tmp', script)
        self.assertNotIn('--bind /tmp', script)
        self.assertIn('TMPDIR=/workspace/tmp', script)  # Inside the home-backed overlay.

    def test_installed_dependency_verifier_detects_changed_bytes_and_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prefix = root / 'opt/apps/deepmd-kit/test/16V100'
            prefix.mkdir(parents=True)
            library = prefix / 'libdeepmd_c.so'
            library.write_bytes(b'accepted library')
            link = prefix / 'alias.so'
            link.symlink_to('libdeepmd_c.so')
            rows = [dict(path='.', kind='directory', mode=stat.S_IMODE(prefix.stat().st_mode)),
                    dict(path=library.name, kind='file', mode=stat.S_IMODE(library.stat().st_mode),
                         size=library.stat().st_size, sha256=hashlib.sha256(library.read_bytes()).hexdigest()),
                    dict(path=link.name, kind='symlink', target='libdeepmd_c.so')]
            dep = dict(identity=dict(install_prefix='/' + str(prefix.relative_to(root))))
            self.assertTrue(standalone.verify_dependency(dep, rows, root)['verified'])
            library.write_bytes(b'changed! library')
            with self.assertRaisesRegex(ValueError, 'content'):
                standalone.verify_dependency(dep, rows, root)
            library.write_bytes(b'accepted library')
            link.unlink()
            link.symlink_to('other.so')
            with self.assertRaisesRegex(ValueError, 'symlink'):
                standalone.verify_dependency(dep, rows, root)

    def test_single_prefix_manifest_keeps_pinned_external_dependency_without_requiring_a_pair(self):
        from export_native import inventory, read_installed_manifests, write_manifests
        from native_module import validate_native_entry
        delivery = self.delivery()
        prefix = delivery['identity']['install_prefix']
        dep = delivery['deepmd_dependency']['identity']['install_prefix']
        entry = dict(identity=delivery['identity'], commands={'lmp': 'bin/lmp'},
                     external_roots=[dep], site_dependencies=[delivery['deepmd_dependency']],
                     runtime=dict(modules=[], prepend={'PATH': [prefix + '/bin'],
                                                       'LD_LIBRARY_PATH': [dep + '/lib']}, set={}))
        self.assertEqual(validate_native_entry(entry), entry)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / prefix.lstrip('/') / 'bin/lmp'
            binary.parent.mkdir(parents=True)
            binary.write_text('#!/bin/sh\nexit 0\n')
            binary.chmod(0o755)
            manifest = write_manifests([entry], root)
            self.assertEqual(manifest, read_installed_manifests([entry], root))
            self.assertEqual(manifest, inventory([entry], root))
            self.assertEqual(len(manifest['entries']), 1)
            self.assertFalse((root / dep.lstrip('/')).exists())
        missing = copy.deepcopy(entry)
        del missing['site_dependencies']
        with self.assertRaisesRegex(ValueError, 'managed application'):
            validate_native_entry(missing)
        changed = copy.deepcopy(entry)
        changed['site_dependencies'] = [self.delivery('4v100-avx512')['deepmd_dependency']]
        with self.assertRaisesRegex(ValueError, 'target'):
            validate_native_entry(changed)
        broad = copy.deepcopy(entry)
        broad['external_roots'].append('/opt/apps/deepmd-kit')
        with self.assertRaisesRegex(ValueError, 'managed application'):
            validate_native_entry(broad)

    def test_installed_mode_does_not_run_the_deepmd_compiler_or_create_dependency_metadata(self):
        # Execute the real shell recipe with tool calls recorded. Its first CMake
        # invocation must be the LAMMPS build, never pip/venv or DeepMD C++.
        recipe = (ROOT / 'controller/md_build.sh').read_text()
        boundary = recipe.index('# Official built-in integration')
        setup = recipe[:boundary]
        setup = setup.replace('source /control/md_environment.sh "$target"', ':')
        setup = setup.replace('md_native_ld=$("$MD_SYSTEM_DEEPMD/bin/python" /control/md_driver_paths.py)', 'md_native_ld=/usr/lib')
        start = setup.index('"$MD_SYSTEM_DEEPMD/bin/python" - <<\'PY\'')
        stop = setup.index('\nPY\n', start) + len('\nPY\n')
        setup = setup[:start] + ': # framework probe is tested separately\n' + setup[stop:]
        setup = setup.replace('"$MD_SYSTEM_DEEPMD/bin/python" /control/md_probe.py', 'record probe')
        setup = setup.replace('module -t list', 'record module').replace('lscpu >', 'record lscpu >')
        setup = setup.replace('gcc -march=native', 'record gcc -march=native').replace('nvcc --version', 'record nvcc --version')
        setup = setup.replace('/workspace', '"$RECIPE_TEST_WORK"')
        # Prefix checks remain in the recipe; remove only that path guard so the
        # behavior test can run without a writable real /opt installation.
        setup = '\n'.join(line for line in setup.splitlines() if not line.startswith('[[ "$DEEPMD_PREFIX" =='))
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            dep = work / 'deepmd'
            dep.mkdir()
            shell = ('record() { printf "%s\\n" "$*" >> "$RECIPE_TEST_WORK/calls"; }; '
                     'cmake() { echo unexpected-cmake >&2; return 1; }; '
                     'export -f record cmake; ' + setup)
            import os
            env = dict(os.environ, DEEPMD_PREFIX=str(dep), LAMMPS_PREFIX=str(work / 'lammps'),
                       MD_SYSTEM_DEEPMD='/missing-no-python', MD_SYSTEM_LAMMPS='/site/lammps',
                       RECIPE_TEST_WORK=str(work))
            subprocess.run(['bash', '-c', shell, 'recipe', '16v100-avx2', 'lammps-installed'], env=env, check=True)
            self.assertEqual(list(dep.iterdir()), [])
            self.assertIn('probe', (work / 'calls').read_text())

    def test_status_uses_accounting_after_build_disappears_from_live_queue(self):
        for state, exit_code, accepted in [('COMPLETED', '0:0', True), ('FAILED', '1:0', False)]:
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / 'results').mkdir()
                (root / 'job.sbatch').write_text('build script')
                (root / 'job.id').write_text('1234\n')
                (root / 'candidate.sif').write_bytes(b'verified candidate')
                (root / 'results/slurm-1234.log').write_text('LAMMPS_STANDALONE_BUILD_VERIFIED_NOT_PUBLISHED\n')
                delivery = dict(identity=dict(target='16v100-avx2'))
                (root / 'request.json').write_text(json.dumps(dict(
                    delivery=delivery, controller=str(standalone.CONTROL),
                    job_script_sha256=standalone.checksum(root / 'job.sbatch'))))

                def scheduler(argv, **kwargs):
                    if argv[0] == 'squeue':
                        self.assertNotIn('-j', argv)
                        return '12340|RUNNING|another-node\n'
                    self.assertEqual(argv[0], 'sacct')
                    return f'1234|{state}|{exit_code}|\n'

                with patch.object(standalone, 'task', return_value=root), \
                        patch.object(standalone, 'validate_delivery', return_value=delivery), \
                        patch.object(standalone, 'dependency', return_value=({}, [])), \
                        patch.object(standalone, 'verify_dependency'), \
                        patch.object(standalone.subprocess, 'check_output', side_effect=scheduler), \
                        redirect_stdout(io.StringIO()):
                    self.assertEqual(standalone.status('archived-build'), 0 if accepted else 1)
                result = json.loads((root / 'results/status.json').read_text())
                self.assertEqual(result['build_verified'], accepted)
                self.assertFalse(result['scientific_verified'])


if __name__ == '__main__':
    unittest.main()
