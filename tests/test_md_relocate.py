from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
from md_relocate_audit import check_dynamic, check_metadata, check_reference, check_symlink


class RuntimePathTests(unittest.TestCase):
    roots = ['/opt/software/lammps/v1/target', '/opt/software/deepmd-kit/v1/target',
             '/opt/apps/conda_env/deepmd-kit-3.2.0', '/usr', '/lib']

    def test_own_origin_and_explicit_system_roots(self):
        check_reference('$ORIGIN/../lib', self.roots, origin=Path(self.roots[0]) / 'bin')
        check_metadata('prefix=/opt/software/lammps/v1/target\nlibdir=${prefix}/lib', self.roots)
        self.assertTrue(check_dynamic('(NEEDED) Shared library: [libstdc++.so.6]', '/opt/x', self.roots))
        self.assertFalse(check_dynamic('There is no dynamic section in this file.', '/opt/x', self.roots))

    def test_build_task_home_new_and_unlisted_site_references(self):
        for value in ['/input/repository/lib', '/runtime/lib', '/home/user/old/lib', '/workspace/lib', '/control/lib',
                      '/opt/apps/conda_env/deepmd-kit-3.2.0.new/lib', '/opt/apps/other/lib', '/opt/software/old/lib']:
            with self.assertRaises(ValueError):
                check_reference(value, self.roots)
            with self.assertRaises(ValueError):
                check_metadata('export LD_LIBRARY_PATH=' + value, self.roots)

    def test_path_needed_empty_and_relative_runpath_rejected(self):
        for text in ['(NEEDED) Shared library: [/opt/lib.so]', '(NEEDED) Shared library: [../lib.so]',
                     '(RUNPATH) Library runpath: [: /usr/lib]', '(RUNPATH) Library runpath: [lib]',
                     '(RUNPATH) Library runpath: [$ORIGIN/../../../../../../input/lib]']:
            with self.assertRaises(ValueError):
                check_dynamic(text, '/opt/software/lammps/v1/target/bin/lmp', self.roots)

    def test_broken_external_and_indirect_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            own = root / 'own'
            own.mkdir()
            target = own / 'lib.so'
            target.write_bytes(b'x')
            good = own / 'good'
            good.symlink_to('lib.so')
            self.assertEqual(check_symlink(good, [own]), target)
            broken = own / 'broken'
            broken.symlink_to('absent')
            outside = root / 'external'
            outside.write_bytes(b'x')
            bad = own / 'bad'
            bad.symlink_to(outside)
            indirect = own / 'indirect'
            indirect.symlink_to(bad)
            for link in (broken, bad, indirect):
                with self.assertRaises(ValueError):
                    check_symlink(link, [own])


if __name__ == '__main__':
    unittest.main()
