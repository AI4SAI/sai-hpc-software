import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'controller'))
from md_registration import GENERATE, INCLUDE, REQUIRED, STYLE_DIR, integrate, verify


class RegistrationTests(unittest.TestCase):
    def test_modern_order_and_idempotence(self):
        original = 'first\n' + STYLE_DIR + '\n' + GENERATE + '\nlast\n'
        expected = original.replace(STYLE_DIR, STYLE_DIR + '\n' + INCLUDE)
        for text in (original, original + INCLUDE + '\n', expected):
            self.assertEqual(integrate(text), expected)
            self.assertEqual(integrate(integrate(text)), expected)

    def test_legacy_order_and_idempotence(self):
        original = 'project(legacy)\n'
        self.assertEqual(integrate(original), original + INCLUDE + '\n')
        self.assertEqual(integrate(integrate(original)), integrate(original))

    def test_rejects_ambiguous_or_changed_boundaries(self):
        text = STYLE_DIR + '\n' + GENERATE + '\n'
        for value in (text + INCLUDE + '\n' + INCLUDE, text + GENERATE,
                      text + STYLE_DIR, GENERATE + '\n' + STYLE_DIR,
                      text.replace(GENERATE, 'GenerateStyleSources(unknown)'),
                      text + INCLUDE.replace('/workspace/', '/other/')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                integrate(value)

    def test_required_fixture_checksum(self):
        path = ROOT / 'tests/fixtures/lammps/StyleHeaderUtils.cmake'
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                         '18164522c46a9010a6db237d5c64cb5d18860382f222810be7434bcdbeef81bb')

    def test_guard_precedes_expensive_build(self):
        text = (ROOT / 'controller/md_build.sh').read_text()
        self.assertLess(text.index('/control/md_registration.py integrate'),
                        text.index('cmake -S /workspace/lammps/cmake'))
        self.assertLess(text.index('/control/md_registration.py verify'),
                        text.index('cmake --build /workspace/lammps-build'))
        self.assertIn('verify_parity', text)

    def test_guard_rejects_missing_or_partial_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            styles = Path(tmp)
            with self.assertRaisesRegex(ValueError, 'missing generated'):
                verify(styles)
            for kind, headers in REQUIRED.items():
                (styles / f'style_{kind}.cpp').write_text('\n'.join(
                    f'add_builtin("{name}", nullptr);' for names in headers.values() for name in names))
            self.assertEqual(verify(styles), {'registered_styles': 13, 'registry': 'source'})
            path = styles / 'style_pair.cpp'
            path.write_text(path.read_text().replace('deepmd/kk/host', 'wrong/kk/host'))
            with self.assertRaisesRegex(ValueError, 'pair:deepmd/kk/host'):
                verify(styles)

    def test_legacy_guard_requires_all_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            styles = Path(tmp)
            for kind, headers in REQUIRED.items():
                (styles / f'style_{kind}.h').write_text('\n'.join(f'#include "{name}"' for name in headers))
            self.assertEqual(verify(styles)['registry'], 'header')
            (styles / 'style_compute.h').write_text('')
            with self.assertRaisesRegex(ValueError, 'compute:deepmd/fparam/dedn'):
                verify(styles)

    def test_real_generator_fails_late_registration_and_passes_repair(self):
        cmake = os.environ.get('RECOVERY_CMAKE') or shutil.which('cmake')
        if not cmake:
            self.skipTest('CMake required for real registry generation')
        utils = ROOT / 'tests/fixtures/lammps/StyleHeaderUtils.cmake'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            headers = root / 'deepmd'
            headers.mkdir()
            for kind, files in REQUIRED.items():
                macro = {'kspace': 'KSPACE'}.get(kind, kind.upper())
                style = {'kspace': 'KSpace'}.get(kind, kind.title())
                for filename, names in files.items():
                    declarations = '\n'.join(f'{style}Style({name}, FixtureClass)' for name in names)
                    (headers / filename).write_text(f'#ifdef {macro}_CLASS\n{declarations}\n#endif\n')
            builtin = headers / 'builtin.cmake'
            builtin.write_text(f'RegisterStyles("{headers}")\nGenerateStyleHeaders(${{LAMMPS_STYLE_HEADERS_DIR}})\n')
            source = ('cmake_minimum_required(VERSION 3.20)\nproject(registry_test NONE)\n'
                      f'include("{utils}")\n' + STYLE_DIR + '\n' + GENERATE + '\n' + INCLUDE + '\n')
            for fixed in (False, True):
                build = root / ('fixed' if fixed else 'broken')
                (build / 'styles').mkdir(parents=True)
                text = integrate(source) if fixed else source
                (root / 'CMakeLists.txt').write_text(text.replace(INCLUDE, f'include("{builtin}")'))
                result = subprocess.run([cmake, '-S', str(root), '-B', str(build)], text=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                self.assertEqual(result.returncode, 0, result.stdout)
                if fixed:
                    self.assertEqual(verify(build / 'styles')['registered_styles'], 13)
                else:
                    with self.assertRaisesRegex(ValueError, 'compute:deepmd/fparam/dedn'):
                        verify(build / 'styles')


if __name__ == '__main__':
    unittest.main()
