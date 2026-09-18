import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import md_link_launcher as launcher


class LinkLauncherTests(unittest.TestCase):
    def test_multiple_cmake_response_files_preserve_order_and_decode_quotes(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = (Path(directory) / name for name in ('objects1.rsp', 'objects2.rsp'))
            a.write_text('plain.cpp.o "CG-DNA/atom_vec_oxdna.cpp.o"\n')
            b.write_text('"CG-SPICA/angle_spica.cpp.o" "path with spaces/file.cpp.o"\n')
            self.assertEqual(launcher.expand_response_files(['-shared', '@' + str(a), '@' + str(b), '-o', 'lib.so']),
                             ['-shared', 'plain.cpp.o', 'CG-DNA/atom_vec_oxdna.cpp.o',
                              'CG-SPICA/angle_spica.cpp.o', 'path with spaces/file.cpp.o', '-o', 'lib.so'])

    def test_nested_responses_and_cycles(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = (Path(directory) / name for name in ('a.rsp', 'b.rsp'))
            a.write_text('@' + str(b))
            b.write_text('"nested.cpp.o"')
            self.assertEqual(launcher.expand_response_files(['@' + str(a)]), ['nested.cpp.o'])
            b.write_text('@' + str(a))
            with self.assertRaisesRegex(ValueError, 'cyclic'):
                launcher.expand_response_files(['@' + str(a)])

    def test_malformed_or_missing_response_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing.rsp'
            with self.assertRaises(FileNotFoundError):
                launcher.expand_response_files(['@' + str(path)])
            path.write_text('"unterminated')
            with self.assertRaises(ValueError):
                launcher.expand_response_files(['@' + str(path)])

    def test_argv_execution_without_shell_or_reinterpretation(self):
        with patch.object(os, 'execvp') as execute:
            launcher.main(['nvcc_wrapper', '-shared', 'a.o', '$(not-a-command).o'])
        execute.assert_called_once_with('nvcc_wrapper', ['nvcc_wrapper', '-shared', 'a.o', '$(not-a-command).o'])
        with self.assertRaises(ValueError):
            launcher.main([])

    def test_link_launcher_is_separate_from_compiler(self):
        build = (Path(__file__).resolve().parents[1] / 'controller/md_build.sh').read_text()
        self.assertIn('-DCMAKE_CXX_LINKER_LAUNCHER=/usr/bin/python3;/control/md_link_launcher.py', build)
        self.assertIn('-DCMAKE_CXX_COMPILER=/workspace/lammps/lib/kokkos/bin/nvcc_wrapper', build)


if __name__ == '__main__':
    unittest.main()
