from copy import deepcopy
import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

CONTROL = Path(__file__).resolve().parents[1] / 'controller'
sys.path.insert(0, str(CONTROL))
from cp2k_spack import validated_prefix
from cp2k_spack_link import ROOTS, check_files, shell_variables, validate_receipt
import cp2k_payload as payload
from cp2k_dependency_stage import render, SEED_SHA256
from release_contract import make_identity


class FinalPrefixTests(unittest.TestCase):
    prefix = '/opt/apps/cp2k/cp2k-development-2026.2-g123-r456/16V100'

    def receipt(self):
        roots = {n: self.prefix + '/dependencies/spack/linux-zen3/' + n + '-hash'
                 for n in sorted(ROOTS)}
        return dict(partition='16V100', install_prefix=self.prefix,
                    dependencies_installed=True, cp2k_built=False, published=False,
                    roots=roots, runtime_prefixes=list(roots.values()),
                    nodes=[dict(name=n, prefix=p, external=False) for n, p in roots.items()])

    def test_prefix_is_explicit_and_partition_matched(self):
        self.assertEqual(validated_prefix('16V100', self.prefix, app_only=True), self.prefix)
        self.assertIn('/opt/software/', validated_prefix('16V100'))
        for bad in (None, self.prefix.replace('16V100', 'DSPRHBM'),
                    self.prefix + '/..', self.prefix.replace('/apps/', '/software/')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validated_prefix('16V100', bad, app_only=True)

    def test_receipt_cannot_relabel_old_store_or_another_partition(self):
        data = self.receipt()
        validate_receipt(data, '16V100', self.prefix)
        for mutate in (
            lambda d: d.update(install_prefix=self.prefix + '-other'),
            lambda d: d.update(partition='DSPRHBM'),
            lambda d: d['roots'].pop('libint'),
            lambda d: d['roots'].update(plumed='/opt/apps/plumed/plumed-2.10.1'),
            lambda d: d['runtime_prefixes'].append('/workspace/spack-stage/lib'),
            lambda d: d['runtime_prefixes'].append(d['runtime_prefixes'][0]),
            lambda d: d['nodes'].pop(),
            lambda d: d['nodes'][0].update(name='wrong-package'),
        ):
            bad = deepcopy(data)
            mutate(bad)
            with self.assertRaises(ValueError):
                validate_receipt(bad, '16V100', self.prefix)

    def test_shell_output_preserves_roots_and_private_library_paths(self):
        data = self.receipt()
        script = shell_variables(data, '16V100', self.prefix)
        result = subprocess.run(['bash', '-eu', '-c', script +
            'printf "%s\\n" "$CP2K_PLUMED_ROOT" "${#cp2k_spack_prefixes[@]}" "$cp2k_spack_runtime_ld"'],
            capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.splitlines()[0], data['roots']['plumed'])
        self.assertEqual(result.stdout.splitlines()[1], str(len(ROOTS)))
        self.assertNotIn('/opt/software', result.stdout)

    def test_plumed_frontend_does_not_substitute_for_kernel(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'lib').mkdir()
            (root / 'lib/libplumed.so').touch()
            roots = {n: str(root) for n in ROOTS}
            with self.assertRaisesRegex(ValueError, 'libplumedKernel'):
                check_files(roots)
            (root / 'lib/libplumedKernel.so').touch()
            check_files(roots)

    def test_fresh_stage_repairs_before_install_without_restage(self):
        script = (CONTROL / 'cp2k_spack_dependencies.sh').read_text()
        self.assertLess(script.index('app_only=True'), script.index('source /control/'))
        self.assertLess(script.index('--fresh-stage'), script.index('install --only-concrete'))
        self.assertIn('--keep-stage --dont-restage', script)
        self.assertIn('--no-cache --fail-fast', script)
        self.assertIn('"$partition" "$prefix"', script)
        self.assertNotIn('--dirty', script)
        self.assertNotIn('/opt/software', script)
        subprocess.run(['bash', '-n', str(CONTROL / 'cp2k_spack_dependencies.sh')], check=True)

    def test_cpu_stage_clones_and_checks_source_only_seed_before_mount(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = make_identity('cp2k', 'development', 'master', 'a' * 40,
                                     '2026.2', 'b' * 64, 'dsprhbm')
            script = render(root, root / 'controller/frozen', root / 'runs/new', identity)
            subprocess.run(['bash', '-n'], input=script, text=True, check=True)
            self.assertIn('--qos=rush-cpu', script)
            self.assertIn('--time=240', script)
            self.assertIn('--cpus-per-task=8', script)
            self.assertIn('--mem=28G', script)
            self.assertIn(SEED_SHA256, script)
            self.assertLess(script.index('cp --sparse=always'), script.index('sha256sum --check'))
            self.assertLess(script.index('sha256sum --check'), script.index('/usr/sbin/resize2fs'))
            self.assertLess(script.index('/usr/sbin/resize2fs'), script.index('cp2k_image_driver.py'))
            self.assertIn('/usr/bin:/bin:ro', script)
            self.assertIn('/input/spack:ro', script)
            self.assertIn('dependencies cp2k', script)
            self.assertIn(identity['install_prefix'], script)
            for absent in ('/opt/software', '--nv ', '--gpus', 'publish', '/opt/apps/cp2k/cp2k-2026.1'):
                self.assertNotIn(absent, script)
            gpu = make_identity('cp2k', 'development', 'master', 'a' * 40,
                                '2026.2', 'b' * 64, '16v100-avx2')
            gpu_script = render(root, root / 'controller/frozen', root / 'runs/new', gpu)
            self.assertIn('--gpus-per-node=1', gpu_script)
            self.assertIn('--qos=flood-1o2gpu', gpu_script)
            self.assertNotIn('--cpus-per-task=', gpu_script)
            with self.assertRaises(ValueError):
                render(root, root / 'controller/frozen', root / 'runs/new', gpu, minutes=241)

    def test_cmake_adapters_resolve_exact_private_lib64_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'lib64').mkdir()
            (root / 'include/libint2').mkdir(parents=True)
            for name in ('libint2.so', 'libxsmm.so'):
                (root / 'lib64' / name).touch()
            (root / 'include/libint2/libint_f.mod').touch()
            (root / 'CMakeLists.txt').write_text(
                'cmake_minimum_required(VERSION 3.20)\nproject(private_roots NONE)\n' +
                '\n'.join('include("' + str(CONTROL / name) + '")' for name in
                          ('cp2k_Libint2Config.cmake', 'cp2k_libxsmmConfig.cmake')) +
                '\nif(NOT TARGET Libint2::int2 OR NOT TARGET libxsmm::libxsmm)\n'
                'message(FATAL_ERROR "missing target")\nendif()\n')
            env = dict(os.environ, CP2K_LIBINT_ROOT=str(root), CP2K_LIBXSMM_ROOT=str(root))
            subprocess.run(['cmake', '-S', str(root), '-B', str(root / 'build')],
                           env=env, capture_output=True, text=True, check=True)
            env['CP2K_LIBINT_ROOT'] = str(root / 'missing')
            result = subprocess.run(['cmake', '-S', str(root), '-B', str(root / 'bad')],
                                    env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Verified CP2K_LIBINT_ROOT', result.stderr)

    def test_payload_keeps_kernel_data_and_paths_but_omits_build_only_packages(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            prefix, export = base / 'installed', base / 'export'
            package = prefix / 'dependencies/spack/linux-zen3/plumed-hash'
            fixtures = ['bin/cp2k.psmp', 'share/sai/spack-dependencies.json',
                        'share/cp2k/data/BASIS_MOLOPT',
                        'dependencies/dbcsr/lib64/libdbcsr.so',
                        'dependencies/libxs/lib/libxs.so', 'dependencies/tblite/lib/libtblite.a',
                        'dependencies/spack/linux-zen3/python-venv-hash/bin/python',
                        'dependencies/spack/linux-zen3/plumed-hash/lib/libplumed.so',
                        'dependencies/spack/linux-zen3/plumed-hash/lib/libplumedKernel.so.1',
                        'dependencies/spack/linux-zen3/plumed-hash/lib/libplumed.a',
                        'dependencies/spack/linux-zen3/plumed-hash/share/plumed/runtime-data',
                        'dependencies/spack/linux-zen3/plumed-hash/share/doc/manual',
                        'dependencies/spack/linux-zen3/plumed-hash/include/header.h',
                        'dependencies/spack/linux-zen3/plumed-hash/.spack/spec.json']
            for relative in fixtures:
                path = prefix / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{}')
            (package / 'lib/libplumedKernel.so').symlink_to('libplumedKernel.so.1')
            with patch.object(payload, 'validate_receipt', return_value=({}, [str(package)])):
                result = payload.stage(prefix, export, '16V100')
                with self.assertRaisesRegex(ValueError, 'already exists'):
                    payload.stage(prefix, export, '16V100')
            relative_package = package.relative_to(prefix)
            self.assertEqual((result / relative_package / 'lib/libplumedKernel.so').read_bytes(), b'{}')
            self.assertTrue((result / relative_package / 'share/plumed/runtime-data').is_file())
            self.assertTrue((result / 'share/cp2k/data/BASIS_MOLOPT').is_file())
            for omitted in ('include', '.spack', 'share/doc', 'lib/libplumed.a'):
                self.assertFalse((result / relative_package / omitted).exists())
            self.assertFalse((result / 'dependencies/spack/linux-zen3/python-venv-hash').exists())
            self.assertTrue((package / 'lib/libplumed.a').exists())  # Build store untouched.


if __name__ == '__main__':
    unittest.main()
