"""Check the admin handoff's file-integrity and symlink boundaries."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import shutil
import subprocess


SOURCE = Path(__file__).resolve().parents[1] / 'deployment/elpa-2026.02.001/install.py'
spec = importlib.util.spec_from_file_location('elpa_site_install', SOURCE)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)
result_spec = importlib.util.spec_from_file_location('elpa_native_results', SOURCE.parent/'verify-native-results.py')
results = importlib.util.module_from_spec(result_spec)
result_spec.loader.exec_module(results)


class ElpaInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'payload'
        (self.root/'lib').mkdir(parents=True)
        library = self.root/'lib/libelpa_openmp.so.19.5.1'
        library.write_bytes(b'ELPA test fixture\n')
        library.chmod(0o644)
        (self.root/'lib/libelpa_openmp.so.19').symlink_to(library.name)

    def test_internal_soname_link_and_exact_bytes(self):
        expected = deploy.inventory(self.root)
        deploy.verify(self.root, expected)
        (self.root/'lib/libelpa_openmp.so.19.5.1').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'differs from manifest'):
            deploy.verify(self.root, expected)

    def test_reject_extra_files(self):
        expected = deploy.inventory(self.root)
        (self.root/'unexpected').write_text('not inventoried')
        with self.assertRaisesRegex(ValueError, 'differs from manifest'):
            deploy.verify(self.root, expected)

    def test_reject_external_and_dangling_links(self):
        link = self.root/'escape'
        outside = Path(self.temp.name)/'outside'
        outside.write_text('outside')
        for target in ['../outside', 'missing', str(outside)]:
            with self.subTest(target=target):
                link.symlink_to(target)
                with self.assertRaises((ValueError, FileNotFoundError)):
                    deploy.inventory(self.root)
                link.unlink()

    def test_reject_symlink_root(self):
        alias = Path(self.temp.name)/'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Symlink in directory path'):
            deploy.inventory(alias)

    def test_reject_writable_shared_payload(self):
        (self.root/'lib/libelpa_openmp.so.19.5.1').chmod(0o666)
        with self.assertRaisesRegex(ValueError, 'Unsafe permissions'):
            deploy.inventory(self.root)


@unittest.skipUnless(shutil.which('tclsh'), 'Tcl interpreter unavailable')
class ElpaModuleTests(unittest.TestCase):
    def selected(self, flags, mode='load', saved=''):
        module = SOURCE.parent/'2026.02.001-2603-gnu-auto'
        harness = r'''
proc ModulesHelp {} {}
proc module-whatis {args} {}
proc conflict {args} {}
proc module {args} {}
proc module-info {kind value} {return [expr {$value eq $::test_mode}]}
proc setenv {name value} {set ::env($name) $value}
proc prepend-path {args} {}
rename exec real_exec
proc exec {args} {return $::test_flags}
rename file real_file
proc file {op args} {
    if {$op eq "isfile"} {return 1}
    return [real_file $op {*}$args]
}
'''
        harness += f'\nset test_flags {{{flags}}}\nset test_mode {mode}\n'
        if saved:
            harness += f'set env(SAI_ELPA_2603_ISA) {saved}\n'
        harness += f'source {{{module}}}\nputs $env(ELPA_ROOT)\n'
        return subprocess.check_output(['tclsh'], input=harness, text=True).strip()

    def test_site_dependency_selection(self):
        for flags, isa in [('avx2 fma', 'avx2'),
                           ('avx2 avx512f avx512dq avx512bw avx512vl', 'avx2'),
                           ('avx2 avx512f avx512_vnni', 'avx512')]:
            with self.subTest(flags=flags):
                self.assertEqual(self.selected(flags),
                    f'/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-{isa}/nvidia')

    def test_unload_uses_loaded_identity(self):
        self.assertIn('-gnu-avx2/nvidia',
                      self.selected('avx2 avx512_vnni', mode='remove', saved='avx2'))


class ElpaNativeResultTests(unittest.TestCase):
    def setUp(self):
        self.log = '\n'.join(
            'ELPA_NUMERIC PASS ranks=2 gpu=1 solver=2 complex=1 nonblocking=1 '
            f'repeat={repeat} spectrum=2.2e-15 residual=6.4e-16 orthogonality=1.7e-15'
            for repeat in (0, 1))

    def parse(self, log):
        return results.parse_case(log, 2, 1, 2, 1, 1)

    def test_complete_repeated_solve(self):
        self.assertEqual(self.parse(self.log)['spectrum'], 2.2e-15)
        self.assertEqual(self.parse('MPI advisory\n\0' + self.log)['spectrum'], 2.2e-15)

    def test_reject_missing_repeat_or_wrong_execution(self):
        for bad in (self.log.splitlines()[0], self.log.replace('ranks=2', 'ranks=1'),
                    self.log.replace('repeat=1', 'repeat=0'), self.log.replace('PASS', 'FAIL')):
            with self.subTest(log=bad), self.assertRaises(ValueError):
                self.parse(bad)

    def test_reject_nonfinite_negative_and_excessive_errors(self):
        for error in ('nan', 'inf', '-1e-15', '1e-10'):
            with self.subTest(error=error), self.assertRaises(ValueError):
                self.parse(self.log.replace('6.4e-16', error))


if __name__ == '__main__':
    unittest.main()
