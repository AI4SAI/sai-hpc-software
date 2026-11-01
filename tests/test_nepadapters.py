"""NEP release gates: numerical validity, GPU identity and minimal deployment."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'controller'))
import nepadapters_compat as compat
import nepadapters_science as science
import nepadapters_native as native
import nepadapters_release as release


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


install = load('nepa_install', ROOT / 'deployment/nepadapters-1.0.4/install.py')
prepare = load('nepa_prepare', ROOT / 'deployment/nepadapters-1.0.4/prepare-release.py')


class NumericalTests(unittest.TestCase):
    def test_nan_infinity_missing_negative_and_wrong_values_fail(self):
        good = dict.fromkeys(science.TOLERANCES, 0.0)
        science.check(good)
        for key in good:
            for value in (float('nan'), float('inf'), -1, True, '0', 1e-2):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    science.check(dict(good, **{key: value}))
            missing = dict(good)
            del missing[key]
            with self.assertRaises(ValueError):
                science.check(missing)

    def test_nan_force_cannot_be_hidden_by_upstream_max(self):
        rows = {1: {'fx': float('nan')}}
        fixture = {'atoms': [{'id': 1}]}
        helper = SimpleNamespace(parse_dump=lambda _: rows, parse_thermo=lambda _: {'Atoms': 1},
                                 compare=lambda *_: dict.fromkeys(science.TOLERANCES, 0.0))
        with self.assertRaisesRegex(ValueError, 'non-finite'):
            science.compare_raw(helper, fixture, Path('.'))
        rows[1]['fx'] = 0.0
        science.compare_raw(helper, fixture, Path('.'))
        rows[2] = {'fx': 0.0}
        with self.assertRaisesRegex(ValueError, 'missing/extra atoms'):
            science.compare_raw(helper, fixture, Path('.'))

    def test_actual_kokkos_banner_and_gpu_backend_required(self):
        banner = ('KOKKOS mode with Kokkos version 5.2.1 is enabled\n'
                  'using double precision\npair_style: nep/gpu, backend: cuda\n'
                  'attributes: full, newton off, kokkos_device')
        science.check_gpu_banner(banner)
        for token in ('is enabled', 'using double precision', 'backend: cuda', 'kokkos_device'):
            with self.assertRaises(ValueError):
                science.check_gpu_banner(banner.replace(token, 'disabled'))


class CompatibilityTests(unittest.TestCase):
    def test_adapts_types_and_communication_without_losing_double_asserts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, lammps = root / 'nepa', root / 'lammps'
            (source / 'frontends/lammps').mkdir(parents=True)
            (lammps / 'src/KOKKOS').mkdir(parents=True)
            (lammps / 'src/KOKKOS/kokkos_type.h').write_text('kkacc_1d double_1d_6_lr')
            header = lammps / 'src/KOKKOS/kokkos.h'
            header.write_text('int reverse_comm_legacy; int reverse_pair_comm_legacy;')
            original = ('static_assert(std::is_same<X_FLOAT, double>::value);\n'
                        'DAT::tdual_xfloat_1d buf; DAT::tdual_virial_array v;\n'
                        'reverse_comm_classic == 0 && reverse_pair_comm_classic == 0;\n')
            for name in ('pair_nep_adapters_cuda.h', 'pair_nep_adapters_cuda.cpp'):
                (source / 'frontends/lammps' / name).write_text(original)
            pins = compat.adapt(source, lammps)
            self.assertEqual(len(pins['files']), 2)
            for name in pins['files']:
                text = (source / 'frontends/lammps' / name).read_text()
                self.assertIn('std::is_same<KK_FLOAT, double>::value', text)
                self.assertIn('DAT::tdual_double_1d_6_lr', text)
                self.assertIn('reverse_comm_legacy == 0 && reverse_pair_comm_legacy == 0', text)
            with self.assertRaisesRegex(ValueError, 'already patched'):
                compat.adapt(source, lammps)
            header.write_text('unexpected API')
            with self.assertRaisesRegex(ValueError, 'communication API'):
                compat.adapt(source, lammps)


class RankTests(unittest.TestCase):
    def test_two_gpu_qos_limit_is_respected_in_both_native_allocations(self):
        plan = dict(request=dict(project=str(release.SAI_PROJECT_ROOT), partition='16V100'),
                    installed=False, submit_home='/home/example',
                    scratch=dict(local='/project/runtime/a', distributed='/project/runtime/b'))
        local = native.render(Path(release.SAI_PROJECT_ROOT) / 'runtime-tests/test', plan, 'local')
        distributed = native.render(Path(release.SAI_PROJECT_ROOT) / 'runtime-tests/test', plan, 'distributed')
        for text, nodes, per_node in ((local, 1, 2), (distributed, 2, 1)):
            self.assertIn('#SBATCH --nodes=' + str(nodes), text)
            self.assertIn('#SBATCH --gpus-per-node=' + str(per_node), text)
            self.assertIn('#SBATCH --qos=flood-1o2gpu', text)
            self.assertIn('export HOME=/home/example', text)

    def test_each_rank_requires_its_own_gpu_and_correct_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for backend, inputs in [('cpu', ('in.baseline',)), ('gpu', ('in.baseline', 'in.dynamics'))]:
                (folder / backend / 'ranks').mkdir(parents=True)
                for name in inputs:
                    for rank in (0, 1):
                        row = dict(rank=rank, local_rank=rank, host='node-a', gpu_uuid='GPU-' + str(rank),
                                   binary='/opt/lmp', binary_sha256='pinned', sm=70)
                        (folder / backend / 'ranks' / f'{name}-{rank}.json').write_text(json.dumps(row))
            native.check_ranks(folder, ['node-a'], 2, '/opt/lmp', 'pinned')
            path = folder / 'gpu/ranks/in.dynamics-1.json'
            row = json.loads(path.read_text())
            for change in (dict(gpu_uuid='GPU-0'), dict(binary='/old/lmp'), dict(host='node-b'), dict(sm=80)):
                path.write_text(json.dumps(dict(row, **change)))
                with self.assertRaises(ValueError):
                    native.check_ranks(folder, ['node-a'], 2, '/opt/lmp', 'pinned')
            path.unlink()
            with self.assertRaisesRegex(ValueError, 'incomplete MPI'):
                native.check_ranks(folder, ['node-a'], 2, '/opt/lmp', 'pinned')


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / 'package'
        self.files = {}
        for name in release.PAYLOAD:
            path = self.package / 'payload/16V100' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('fixture ' + name).encode())
            path.chmod(0o644)
            self.files[name] = dict(sha256=install.digest(path), size=path.stat().st_size, mode=0o644)

    def test_payload_rejects_extra_files_corruption_and_symlinks(self):
        payload = self.package / 'payload/16V100'
        install.verify_payload(payload, self.files)
        extra = payload / 'custom-test.py'
        extra.write_text('not a runtime file')
        with self.assertRaisesRegex(ValueError, 'extra files'):
            install.verify_payload(payload, self.files)
        extra.unlink()
        library = payload / 'lib/nepadaptersgpuplugin.so'
        original = library.read_bytes()
        library.write_bytes(b'wrong library')
        with self.assertRaisesRegex(ValueError, 'differs'):
            install.verify_payload(payload, self.files)
        library.unlink()
        external = self.root / 'external'
        external.write_bytes(original)
        library.symlink_to(external)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            install.verify_payload(payload, self.files)

    def test_minimal_install_is_readable_with_private_umask_and_repeatable(self):
        module = self.root / 'opt/modules/nepadapters/1.0.4'
        prefix = self.root / 'opt/apps/nepadapters/1.0.4/16V100'
        (self.package / 'modulefile').write_text('#%Module1.0\n')
        plan = dict(partitions={'16V100': dict(prefix=str(prefix), files=self.files)},
                    module_sha256=install.digest(self.package / 'modulefile'))
        previous = os.umask(0o077)
        try:
            with patch.object(install, 'PACKAGE', self.package), patch.object(install, 'MODULE', module), \
                    contextlib.redirect_stdout(io.StringIO()):
                install.install(plan)
                install.install(plan)
        finally:
            os.umask(previous)
        install.verify_payload(prefix, self.files)
        self.assertEqual(module.stat().st_mode & 0o777, 0o644)
        self.assertEqual({p.name for p in module.parent.iterdir()}, {'1.0.4'})
        for path in prefix.rglob('*'):
            if path.is_dir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o755)

    def test_incomplete_partition_set_cannot_prepare_release(self):
        with self.assertRaisesRegex(ValueError, 'all three'):
            prepare.prepare([], self.root / 'release')
        self.assertFalse((self.root / 'release').exists())

    def test_module_extension_only_replaces_the_known_previous_module(self):
        module = self.root / 'modules/nepadapters/1.0.4'
        module.parent.mkdir(parents=True)
        module.write_text('#%Module1.0\n# previous accepted partitions\n')
        old_sha = install.digest(module)
        (self.package / 'modulefile').write_text('#%Module1.0\n# additional accepted partition\n')
        plan = dict(partitions={}, module_sha256=install.digest(self.package / 'modulefile'),
                    previous_module_sha256=[old_sha])
        with patch.object(install, 'PACKAGE', self.package), patch.object(install, 'MODULE', module), \
                contextlib.redirect_stdout(io.StringIO()):
            install.install(plan)
            self.assertEqual(module.read_bytes(), (self.package / 'modulefile').read_bytes())
            module.write_text('unrelated administrator content')
            with self.assertRaisesRegex(ValueError, 'module changed'):
                install.install(plan)
            self.assertEqual(module.read_text(), 'unrelated administrator content')
        self.assertEqual({p.name for p in module.parent.iterdir()}, {'1.0.4'})

    @unittest.skipUnless(shutil.which('tclsh'), 'Tcl required')
    def test_module_selects_partition_and_preserves_plugin_path(self):
        plugin = self.package / 'payload/16V100'
        row = {'prefix': str(plugin), 'lammps': {'prefix': '/opt/lammps-release'}}
        module = self.root / 'modulefile'
        module.write_text(prepare.modulefile({'16V100': row}))
        setup = '''proc module-whatis {args} {}
proc conflict {args} {}
proc depends-on {args} {}
proc module-info {type value} {expr {$value eq $::mode}}
proc prepend-path {key value} {set ::env($key) "$value:$::env($key)"}
proc setenv {key value} {set ::env($key) $value}
set mode load
set env(SLURM_JOB_ID) 123
set env(SLURM_JOB_PARTITION) 16V100
set env(SAI_LAMMPS_PREFIX) /opt/lammps-release
set env(LAMMPS_PLUGIN_PATH) /existing/plugins
'''
        result = subprocess.run(['tclsh'], input=setup + f'source {{{module}}}\nputs $env(LAMMPS_PLUGIN_PATH)\n',
                                text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.strip(), str(plugin / 'lib') + ':/existing/plugins')
        for change in ('set env(SAI_LAMMPS_PREFIX) /opt/wrong\n', 'unset env(SLURM_JOB_ID)\n',
                       'set env(SLURM_JOB_PARTITION) unsupported\n'):
            script = setup + change + f'if {{![catch {{source {{{module}}}}} message]}} {{exit 2}}\nputs $message\n'
            result = subprocess.run(['tclsh'], input=script, text=True, capture_output=True, check=True)
            self.assertTrue(result.stdout.strip())


if __name__ == '__main__':
    unittest.main()
