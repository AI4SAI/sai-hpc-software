"""Exercise partition dispatch and CPU deployment inventory boundaries."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parents[1]/'deployment/elpa-2026.02.001-cpu'
spec = importlib.util.spec_from_file_location('elpa_cpu_install', PACKAGE/'install.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


@unittest.skipUnless(shutil.which('tclsh'), 'Tcl unavailable')
class PartitionModuleTests(unittest.TestCase):
    def evaluate(self, partition='', flags='avx2 avx512_vnni', mode='load', saved=''):
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
unset -nocomplain env(SLURM_JOB_PARTITION) env(SAI_ELPA_2603_PARTITION) env(ELPA_ROOT)
'''
        script += f'set test_mode {{{mode}}}\nset test_flags {{{flags}}}\n'
        if partition:
            script += f'set env(SLURM_JOB_PARTITION) {{{partition}}}\n'
        if saved:
            script += f'set env(SAI_ELPA_2603_PARTITION) {{{saved}}}\n'
        script += 'if {[catch {source {'+str(PACKAGE/'2026.02.001-2603-gnu-partition')+'}} problem]} {puts stderr $problem; exit 1}\n'
        script += 'if {[info exists env(ELPA_ROOT)]} {puts "$env(ELPA_ROOT)|$env(SAI_ELPA_2603_BACKEND)"}\n'
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


if __name__ == '__main__':
    unittest.main()
