"""Exercise physical deployment, pruning and partition selection without /opt writes."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parents[1] / 'deployment/cp2k-2026.2'
spec = importlib.util.spec_from_file_location('cp2k_site_deploy', PACKAGE / 'install.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class SiteDeploymentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.system = self.directory / 'system'
        self.system.mkdir()
        self.release = json.loads((PACKAGE / 'release.json').read_text())

    def fixture(self, index=0, extra=None):
        spec = self.release['deliveries'][index]
        prefix = deploy.APP_ROOT / ('cp2k-release-' + spec['build_id']) / spec['partition']
        source = self.directory / ('source-' + spec['partition'])
        source.mkdir()
        files = {
            'bin/cp2k.psmp': b'accepted executable',
            'bin/graph.psmp': b'user utility',
            'bin/grid_unittest.psmp': b'test binary',
            'bin/grid_miniapp.psmp': b'benchmark binary',
            'lib64/libcp2k.so.2026.2': b'application library',
            'lib64/cmake/cp2kConfig.cmake': b'build config',
            'share/sai/native-module.tcl': b'#%Module1.0\n',
            'share/cp2k/data/BASIS_MOLOPT': b'basis',
            'share/cp2k/data/POTENTIAL_UZH_2026.2': b'potentials',
            'dependencies/elpa/lib/libelpa.so.21': b'elpa library',
            'dependencies/elpa/share/sai/config.log': b'configure log',
            'dependencies/spack/plumed/lib/libplumedKernel.so': b'dynamic plugin',
            'dependencies/spack/plumed/lib/plumed/src/config/Config.inc': b'runtime support',
            'dependencies/spack/plumed/lib/plumed/patches/gromacs.diff': b'integration patch',
            'dependencies/spack/plumed/lib/plumed/vim/keywords.vim': b'editor data',
        }
        files.update(extra or {})
        for name, content in files.items():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            path.chmod(0o755 if name.startswith('bin/') else 0o644)
        (source / 'bin/cp2k.popt').symlink_to('cp2k.psmp')
        (source / 'share/cp2k/data/POTENTIAL_UZH').symlink_to('POTENTIAL_UZH_2026.2')
        for path in [source, *source.rglob('*')]:
            if path.is_dir():
                path.chmod(0o755)
        rows = []
        for path in [source, *source.rglob('*')]:
            row = {'path': str(prefix / path.relative_to(source)).lstrip('/'), 'mode': 0o755}
            if path.is_symlink():
                row.update(kind='symlink', target=os.readlink(path))
            elif path.is_dir():
                row['kind'] = 'directory'
            else:
                row.update(kind='file', mode=path.stat().st_mode & 0o777,
                           size=path.stat().st_size, sha256=deploy.checksum(path))
            rows.append(row)
        identity = {'install_prefix': str(prefix), 'partition': spec['partition']}
        manifest = {'entry': {'identity': identity, 'external_roots': []}, 'files': rows}
        records, removed = deploy.runtime_records(manifest, prefix)
        receipt = {'schema': 1, 'policy': deploy.POLICY, 'identity': identity,
                   'image_sha256': spec['image_sha256'],
                   'source_native_manifest_sha256': spec['manifest_sha256'],
                   'files': records, 'omitted_files': removed}
        return {'source': source, 'prefix': prefix, 'receipt': receipt}, manifest

    def install(self, deliveries):
        with contextlib.redirect_stdout(io.StringIO()):
            deploy.install(deliveries, self.system)

    def test_keep_complete_data_libraries_runtime_support_and_user_tools(self):
        delivery, _ = self.fixture()
        paths = {row['path'] for row in delivery['receipt']['files']}
        for name in ('bin/cp2k.psmp', 'bin/cp2k.popt', 'bin/graph.psmp',
                     'share/cp2k/data/POTENTIAL_UZH', 'share/cp2k/data/POTENTIAL_UZH_2026.2',
                     'dependencies/spack/plumed/lib/libplumedKernel.so',
                     'dependencies/spack/plumed/lib/plumed/src/config/Config.inc'):
            self.assertIn(name, paths)
        for name in ('bin/grid_unittest.psmp', 'bin/grid_miniapp.psmp',
                     'lib64/cmake/cp2kConfig.cmake', 'dependencies/elpa/share/sai/config.log',
                     'dependencies/spack/plumed/lib/plumed/patches/gromacs.diff',
                     'dependencies/spack/plumed/lib/plumed/vim/keywords.vim'):
            self.assertNotIn(name, paths)
        self.assertFalse(any('examples' in name or 'benchmarks' in name for name in paths))

    def test_pruning_cannot_leave_a_dangling_runtime_link(self):
        delivery, manifest = self.fixture()
        for row in manifest['files']:
            if row['path'].endswith('/bin/cp2k.popt'):
                row['target'] = 'grid_unittest.psmp'
        with self.assertRaisesRegex(ValueError, 'break runtime link'):
            deploy.runtime_records(manifest, delivery['prefix'])

    def test_four_partition_install_is_complete_and_idempotent(self):
        deliveries = [self.fixture(index)[0] for index in range(4)]
        self.install(deliveries)
        module = deploy.physical_prefix(self.system, deploy.MODULE)
        self.assertEqual(module.read_bytes(), (PACKAGE / '2026.2-auto').read_bytes())
        binary = deploy.physical_prefix(self.system, deliveries[0]['prefix']) / 'bin/cp2k.psmp'
        inode = binary.stat().st_ino
        self.install(deliveries)
        self.assertEqual(binary.stat().st_ino, inode)
        with contextlib.redirect_stdout(io.StringIO()):
            deploy.verify_installed(self.system)
        for delivery in deliveries:
            root = deploy.physical_prefix(self.system, delivery['prefix'])
            self.assertEqual(os.readlink(root / 'bin/cp2k.popt'), 'cp2k.psmp')
            self.assertFalse((root / 'bin/grid_unittest.psmp').exists())
            self.assertTrue((delivery['source'] / 'bin/grid_unittest.psmp').exists())

    def test_conflicting_installation_stops_before_other_prefixes_or_module(self):
        deliveries = [self.fixture(index)[0] for index in range(4)]
        conflict = deploy.physical_prefix(self.system, deliveries[-1]['prefix'])
        conflict.mkdir(parents=True)
        (conflict / 'local-file').write_text('preserve')
        with self.assertRaises(ValueError):
            self.install(deliveries)
        self.assertEqual((conflict / 'local-file').read_text(), 'preserve')
        self.assertFalse(deploy.physical_prefix(self.system, deliveries[0]['prefix']).exists())
        self.assertFalse(deploy.physical_prefix(self.system, deploy.MODULE).exists())

    def test_administrator_private_umask_does_not_hide_public_installation(self):
        deliveries = [self.fixture(index)[0] for index in range(4)]
        previous = os.umask(0o077)
        try:
            self.install(deliveries)
        finally:
            os.umask(previous)
        for delivery in deliveries:
            prefix = deploy.physical_prefix(self.system, delivery['prefix'])
            self.assertEqual(prefix.parent.stat().st_mode & 0o777, 0o755)
            self.assertEqual(prefix.stat().st_mode & 0o777, 0o755)
        self.assertEqual(deploy.physical_prefix(self.system, deploy.MODULE).stat().st_mode & 0o777, 0o644)

    def test_changed_source_never_publishes_prefix_or_module(self):
        delivery, _ = self.fixture()
        (delivery['source'] / 'lib64/libcp2k.so.2026.2').write_bytes(b'corrupt source')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.install([delivery])
        self.assertFalse(deploy.physical_prefix(self.system, delivery['prefix']).exists())
        self.assertFalse(deploy.physical_prefix(self.system, deploy.MODULE).exists())

    def test_existing_parent_symlink_is_rejected(self):
        delivery, _ = self.fixture()
        outside = self.directory / 'outside'
        outside.mkdir()
        (self.system / 'opt').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symbolic link'):
            self.install([delivery])
        self.assertEqual(list(outside.iterdir()), [])

    def test_installed_corruption_is_reported(self):
        deliveries = [self.fixture(index)[0] for index in range(4)]
        self.install(deliveries)
        data = deploy.physical_prefix(self.system, deliveries[0]['prefix']) / 'share/cp2k/data/BASIS_MOLOPT'
        data.write_bytes(b'broken')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            deploy.verify_installed(self.system)

    @unittest.skipUnless(shutil.which('tclsh'), 'Tcl interpreter required')
    def test_module_show_and_saved_partition_unload(self):
        script = (PACKAGE / '2026.2-auto').read_text()
        for item in self.release['deliveries']:
            prefix = deploy.APP_ROOT / ('cp2k-release-' + item['build_id']) / item['partition']
            fragment = deploy.physical_prefix(self.system, prefix) / 'share/sai/native-module.tcl'
            fragment.parent.mkdir(parents=True)
            fragment.write_text('set selected_partition ' + item['partition'] + '\n')
        selector = self.directory / 'selector.tcl'
        selector.write_text(script.replace('/opt/apps/cp2k', str(self.system / 'opt/apps/cp2k')))
        common = '''
proc module-whatis {args} {}
proc module-info {unused wanted} {global mode; return [expr {$wanted eq $mode}]}
'''
        for item in self.release['deliveries']:
            partition = item['partition']
            code = common + f'''
set mode load
set env(SLURM_JOB_PARTITION) {partition}
source {{{selector}}}
if {{$selected_partition ne "{partition}"}} {{error "wrong load partition"}}
set env(SAI_CP2K_NATIVE_PARTITION) {partition}
set env(SLURM_JOB_PARTITION) changed-after-load
set mode remove
source {{{selector}}}
if {{$selected_partition ne "{partition}"}} {{error "wrong unload partition"}}
puts PASS
'''
            result = subprocess.run(['tclsh'], input=code, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, '')
            self.assertEqual(result.stdout.strip(), 'PASS')
        code = common + f'set mode display\nunset -nocomplain env(SLURM_JOB_PARTITION)\nsource {{{selector}}}\nputs PASS\n'
        result = subprocess.run(['tclsh'], input=code, text=True, capture_output=True)
        self.assertEqual(result.stderr, '')
        self.assertEqual(result.stdout.strip(), 'PASS')


if __name__ == '__main__':
    unittest.main()
