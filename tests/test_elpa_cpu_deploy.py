"""Exercise partition dispatch and CPU deployment inventory boundaries."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

PACKAGE = Path(__file__).resolve().parents[1]/'deployment/elpa-2026.02.001-cpu'
spec = importlib.util.spec_from_file_location('elpa_cpu_install', PACKAGE/'install.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)
result_spec = importlib.util.spec_from_file_location(
    'elpa_cpu_native_results', PACKAGE.with_name('elpa-2026.02.001')/'verify-native-results.py')
native_results = importlib.util.module_from_spec(result_spec)
result_spec.loader.exec_module(native_results)


@unittest.skipUnless(shutil.which('tclsh'), 'Tcl unavailable')
class PartitionModuleTests(unittest.TestCase):
    def evaluate(self, partition='', flags='avx2 avx512_vnni', mode='load', saved='', legacy_isa=''):
        script = r'''
proc module-whatis {args} {}
proc conflict {args} {}
proc module {args} {}
proc module-info {kind value} {expr {$value eq $::test_mode}}
proc setenv {name value} {set ::env($name) $value}
proc prepend-path {args} {}
rename exec real_exec
proc exec {args} {return $::test_flags}
rename file real_file
proc file {op args} {
    if {$op eq "isfile"} {return 1}
    return [real_file $op {*}$args]
}
unset -nocomplain env(SLURM_JOB_PARTITION) env(SAI_ELPA_2603_PARTITION) env(ELPA_ROOT) env(SAI_ELPA_2603_ISA) env(SAI_ELPA_2603_BACKEND)
'''
        script += f'set test_mode {{{mode}}}\nset test_flags {{{flags}}}\n'
        if partition:
            script += f'set env(SLURM_JOB_PARTITION) {{{partition}}}\n'
        if saved:
            script += f'set env(SAI_ELPA_2603_PARTITION) {{{saved}}}\n'
        if legacy_isa:
            script += f'set env(SAI_ELPA_2603_ISA) {{{legacy_isa}}}\n'
            script += f'set env(ELPA_ROOT) /opt/devtools/elpa/elpa-2026.02.001-2603-gnu-{legacy_isa}/nvidia\n'
        script += 'if {[catch {source {'+str(PACKAGE/deploy.NAME)+'}} problem]} {puts stderr $problem; exit 1}\n'
        script += 'if {[info exists env(ELPA_ROOT)]} {puts "$env(ELPA_ROOT)|$backend"}\n'
        return subprocess.run(['tclsh'], input=script, text=True, capture_output=True)

    def test_partition_and_backend_selection(self):
        for partition, flags, isa, backend in [
                ('DSPRHBM','avx2 avx512_vnni','avx512','cpu'),
                ('4V100','avx2 avx512_vnni','avx512','nvidia'),
                ('16V100','avx2','avx2','nvidia'),
                ('8V100V0','avx2 avx512f','avx2','nvidia')]:
            with self.subTest(partition=partition):
                result = self.evaluate(partition, flags)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(),
                    f'/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-{isa}/{backend}|{backend}')

    def test_reject_missing_or_unknown_partition_and_wrong_node(self):
        for partition, flags in [('', 'avx2'), ('unknown', 'avx2'),
                                 ('DSPRHBM','avx2'), ('16V100','avx2 avx512_vnni')]:
            with self.subTest(partition=partition, flags=flags):
                self.assertNotEqual(self.evaluate(partition, flags).returncode, 0)

    def test_unload_keeps_original_cpu_identity(self):
        result = self.evaluate('16V100', 'avx2', 'remove', 'DSPRHBM')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.strip().endswith('/cpu|cpu'))


    def test_unload_previous_auto_after_in_place_upgrade(self):
        for isa in ('avx2', 'avx512'):
            with self.subTest(isa=isa):
                result = self.evaluate('DSPRHBM', '', 'remove', legacy_isa=isa)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(),
                    f'/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-{isa}/nvidia|nvidia')
        self.assertNotEqual(self.evaluate(mode='remove').returncode, 0)


class AutoModuleUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.package = root/'package'
        self.package.mkdir()
        self.module = root/'modules'/deploy.NAME
        self.module.parent.mkdir()
        self.module.write_bytes(b'previous accepted auto module\n')
        (self.package/deploy.NAME).write_bytes(b'new partition-aware auto module\n')
        self.release = dict(module_name=deploy.NAME, module_sha256=deploy.checksum(self.package/deploy.NAME),
                            previous_module_sha256=deploy.checksum(self.module))
        self.enterContext(mock.patch.multiple(deploy, PACKAGE=self.package, MODULE=self.module))

    def test_replace_known_version_preserve_backup_and_repeat(self):
        previous = self.module.read_bytes()
        deploy.install_module(self.release)
        backup = self.module.with_name(f'.{deploy.NAME}.before-{self.release["previous_module_sha256"]}')
        self.assertEqual(backup.read_bytes(), previous)
        self.assertEqual(deploy.checksum(self.module), self.release['module_sha256'])
        self.assertEqual(self.module.stat().st_mode & 0o777, 0o644)
        inode = self.module.stat().st_ino
        deploy.install_module(self.release)
        self.assertEqual(self.module.stat().st_ino, inode)
        self.assertEqual(len(list(self.module.parent.iterdir())), 2)

    def test_refuse_unrecognized_or_symlink_module(self):
        self.module.write_text('administrator edits')
        with self.assertRaisesRegex(ValueError, 'both accepted versions'):
            deploy.install_module(self.release)
        self.assertEqual(self.module.read_text(), 'administrator edits')
        self.module.unlink()
        self.module.symlink_to(self.package/deploy.NAME)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            deploy.install_module(self.release)

    def test_failed_replace_keeps_old_module_and_no_staging_file(self):
        previous = self.module.read_bytes()
        with mock.patch.object(deploy.os, 'replace', side_effect=OSError('simulated failure')):
            with self.assertRaises(OSError):
                deploy.install_module(self.release)
        self.assertEqual(self.module.read_bytes(), previous)
        self.assertFalse(list(self.module.parent.glob('*.install-*')))


class CpuInventoryTests(unittest.TestCase):
    def test_detect_changed_bytes_extra_files_and_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'cpu'
            (root/'lib').mkdir(parents=True)
            library = root/'lib/libelpa_openmp.so.19'
            library.write_bytes(b'cpu library fixture')
            expected = deploy.inventory(root)
            deploy.verify(root, expected)
            library.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                deploy.verify(root, expected)
            library.write_bytes(b'cpu library fixture')
            (root/'lib/escape').symlink_to('../../outside')
            (Path(tmp)/'outside').write_text('outside')
            with self.assertRaises(ValueError):
                deploy.inventory(root)


class NativePartitionEvidenceTests(unittest.TestCase):
    evidence = PACKAGE.parents[1]/'docs/evidence/elpa-auto-native-20261019'

    def test_accepted_physical_cpu_and_gpu_results(self):
        report = native_results.validate(self.evidence, release_path=PACKAGE/'release.json')
        self.assertEqual(report['total_solves'], 168)
        self.assertEqual(report['unaccepted_partitions'], [])
        self.assertEqual(report['partitions']['DSPRHBM']['backend'], 'cpu')
        self.assertEqual(report['partitions']['DSPRHBM']['numerical_solves'], 24)

    def test_refuse_wrong_cpu_backend_api_module_and_cuda_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'evidence'
            root.mkdir()
            shutil.copytree(self.evidence/'control', root/'control')
            shutil.copytree(self.evidence/'DSPRHBM', root/'DSPRHBM')
            for name in ['controls.sha256', 'accounting.txt']:
                shutil.copyfile(self.evidence/name, root/name)
            changes = [('backend.txt', 'nvidia\n'), ('c-api.txt', 'host\n'),
                       ('modulefile.txt', '/home/user/modulefiles/elpa/2026.02.001-2603-gnu-auto\n'),
                       ('dynamic.txt', ' (NEEDED) Shared library: [libcuda.so.1]\n'),
                       ('symbols.txt', ' U cudaPointerGetAttributes\n')]
            for name, bad in changes:
                path = root/'DSPRHBM'/name
                original = path.read_text()
                with self.subTest(name=name):
                    path.write_text(bad)
                    try:
                        with self.assertRaises(ValueError):
                            native_results.validate(root, ['DSPRHBM'], release_path=PACKAGE/'release.json')
                    finally:
                        path.write_text(original)


if __name__ == '__main__':
    unittest.main()
