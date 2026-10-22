"""Exercise physical deployment, pruning and partition selection without /opt writes."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock


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

    def upgrade_package(self):
        package = self.directory / 'upgrade'
        package.mkdir()
        previous = (PACKAGE / '2026.2-auto').read_bytes()
        desired = PACKAGE.with_name('cp2k-2026.2-shared-elpa') / '2026.2-auto'
        shutil.copyfile(desired, package / '2026.2-auto')
        release = {**self.release, 'previous_module_sha256': hashlib.sha256(previous).hexdigest(),
                   'module_sha256': deploy.checksum(package / '2026.2-auto')}
        (package / 'release.json').write_bytes(deploy.canonical(release))
        module = deploy.physical_prefix(self.system, deploy.MODULE)
        module.parent.mkdir(parents=True)
        module.write_bytes(previous)
        module.chmod(0o644)
        return package, release, module, previous

    def test_upgrade_retains_old_module_and_publishes_only_after_all_payloads_pass(self):
        deliveries = [self.fixture(index)[0] for index in range(4)]
        package, release, module, previous = self.upgrade_package()
        with mock.patch.object(deploy, 'PACKAGE', package):
            self.install(deliveries)
            self.assertEqual(module.read_bytes(), (package / '2026.2-auto').read_bytes())
            backup = module.with_name('.2026.2-auto.before-' + release['previous_module_sha256'][:16])
            self.assertEqual(backup.read_bytes(), previous)
            self.install(deliveries)
            with contextlib.redirect_stdout(io.StringIO()):
                deploy.verify_installed(self.system)
            self.assertEqual(backup.read_bytes(), previous)

    def test_upgrade_failure_preserves_active_module_and_rejects_unknown_module(self):
        deliveries = [self.fixture(index)[0] for index in range(4)]
        package, _, module, previous = self.upgrade_package()
        (deliveries[-1]['source'] / 'bin/cp2k.psmp').write_bytes(b'corrupt')
        with mock.patch.object(deploy, 'PACKAGE', package):
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                self.install(deliveries)
            self.assertEqual(module.read_bytes(), previous)
            module.write_bytes(b'local administrator change')
            with self.assertRaisesRegex(ValueError, 'existing site module differs'):
                self.install(deliveries)
            self.assertEqual(module.read_bytes(), b'local administrator change')

    def test_shared_dependency_mismatch_blocks_upgrade_before_copying(self):
        deliveries = [self.fixture(index)[0] for index in range(4)]
        package, release, module, previous = self.upgrade_package()
        lock = package / 'shared-elpa.json'
        lock.write_bytes(deploy.canonical({'variants': {'gpu': {'prefix': '/opt/devtools/elpa/test',
            'files': {'lib/libelpa.so.1': '0'*64}, 'symlinks': {}}}}))
        release['shared_elpa_sha256'] = deploy.checksum(lock)
        (package / 'release.json').write_bytes(deploy.canonical(release))
        with mock.patch.object(deploy, 'PACKAGE', package):
            with self.assertRaisesRegex(ValueError, 'shared ELPA changed'):
                self.install(deliveries)
        self.assertEqual(module.read_bytes(), previous)
        self.assertFalse(deploy.physical_prefix(self.system, deliveries[0]['prefix']).exists())

    @unittest.skipUnless(shutil.which('tclsh'), 'Tcl interpreter required')
    def test_upgraded_selector_loads_new_partition_and_unloads_previously_loaded_build(self):
        new_package = PACKAGE.with_name('cp2k-2026.2-shared-elpa')
        selector = self.directory / 'new-selector.tcl'
        selector.write_text((new_package / '2026.2-auto').read_text().replace(
            '/opt/apps/cp2k', str(self.system / 'opt/apps/cp2k')))
        for entry in self.release['deliveries']:
            partition = entry['partition']
            old = self.system / 'opt/apps/cp2k' / ('cp2k-release-' + entry['build_id']) / partition
            new = self.system / 'opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43' / partition
            for prefix in (old, new):
                fragment = prefix / 'share/sai/native-module.tcl'
                fragment.parent.mkdir(parents=True)
                fragment.write_text('set selected {' + str(prefix) + '}\n')
            code = '''
proc module-whatis {args} {}
proc module-info {unused wanted} {global mode; return [expr {$wanted eq $mode}]}
''' + f'''
set mode load
set env(SLURM_JOB_PARTITION) {partition}
source {{{selector}}}
if {{$selected ne "{new}"}} {{error "wrong new prefix"}}
set mode remove
set env(SAI_CP2K_NATIVE_PARTITION) {partition}
set env(SLURM_JOB_PARTITION) changed
foreach prefix {{{old} {new}}} {{
    set env(SAI_CP2K_PREFIX) $prefix
    source {{{selector}}}
    if {{$selected ne $prefix}} {{error "unload selected a different build"}}
}}
puts PASS
'''
            result = subprocess.run(['tclsh'], input=code, text=True, capture_output=True)
            self.assertEqual(result.stderr, '')
            self.assertEqual(result.stdout.strip(), 'PASS')

    def test_cross_account_checks_write_to_own_home_and_keep_references_read_only(self):
        reference = self.directory / 'other-user-reference'
        reference.mkdir(mode=0o555)
        tools = self.directory / 'tools'
        tools.mkdir()
        commands = {
            'python3': '#!/usr/bin/env bash\n[[ $1 == */install.py && $2 == --verify ]]\n',
            'sbatch': '#!/usr/bin/env bash\nprintf "%s\\0" "$@" > "$SAI_TEST_SBATCH_ARGS"\nprintf "12345\\n"\n',
        }
        for name, content in commands.items():
            path = tools / name
            path.write_text(content)
            path.chmod(0o755)
        for override in (False, True):
            with self.subTest(override=override):
                home = self.directory / ('user home ' + str(override))
                home.mkdir()
                args = home / 'sbatch.args'
                environment = {**os.environ, 'HOME': str(home),
                               'PATH': str(tools) + os.pathsep + os.environ['PATH'],
                               'SAI_SOFTWARE_ROOT': str(reference), 'SAI_TEST_SBATCH_ARGS': str(args)}
                environment.pop('SAI_CP2K_NATIVE_WORK_ROOT', None)
                work = home / 'sai-hpc-software'
                if override:
                    work = home / 'short-work'
                    environment['SAI_CP2K_NATIVE_WORK_ROOT'] = str(work)
                result = subprocess.run(['bash', str(PACKAGE / 'submit-native-checks.sh'), '4V100'],
                                        env=environment, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                results = list((work / 'runtime-tests').glob('cp2k-2026.2-physical-*'))
                self.assertEqual(len(results), 1)
                output = results[0] / '4V100'
                self.assertEqual((output / 'job.id').read_text(), '12345\n')
                arguments = args.read_bytes().decode().split('\0')[:-1]
                self.assertEqual(arguments[-5:], [str(PACKAGE / 'verify-native.sbatch'),
                                                 str(PACKAGE), str(reference), str(output), str(work)])
                self.assertIn(str(output), (results[0] / 'jobs.tsv').read_text())
                self.assertEqual(list(reference.iterdir()), [])

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
