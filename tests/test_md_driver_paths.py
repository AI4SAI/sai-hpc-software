from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'controller'))
from md_driver_paths import FAKEROOT_FILES, INJECTED, REQUIRED, normalize
from md_relocate_audit import check_metadata


class DriverPathsTests(unittest.TestCase):
    def fixture(self, root):
        injected = root / INJECTED.lstrip('/')
        native = root / 'usr/lib/x86_64-linux-gnu'
        injected.mkdir(parents=True)
        native.mkdir(parents=True)
        for name in REQUIRED:
            (native / name).write_bytes(b'\x7fELF' + name.encode())
            (injected / name).write_bytes((native / name).read_bytes())
        return injected, native

    def test_identical_drivers_replace_injected_without_reordering_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            old = '/opt/devtools/mpi/lib:' + INJECTED + ':/opt/apps/conda_env/deepmd-kit-3.2.0/lib'
            value, evidence = normalize(old, root=root)
            self.assertEqual(value, old.replace(INJECTED, '/usr/lib/x86_64-linux-gnu'))
            self.assertTrue(evidence['changed'])
            self.assertEqual(len(evidence['libraries']), 3)
            self.assertNotIn('.singularity', value)
            self.assertEqual(normalize(value, root=root)[0], value)
            check_metadata('export LD_LIBRARY_PATH=' + value,
                           ['/usr', '/opt/devtools', '/opt/apps/conda_env/deepmd-kit-3.2.0'])

    def test_missing_or_different_native_library_is_not_silently_dropped(self):
        for failure in ('missing', 'different', 'nonelf', 'escape'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _, native = self.fixture(root)
                path = native / REQUIRED[0]
                if failure == 'missing':
                    path.unlink()
                elif failure == 'different':
                    path.write_bytes(b'\x7fELFwrong-driver')
                elif failure == 'nonelf':
                    path.write_bytes(b'not a driver')
                else:
                    outside = root / 'outside.so'
                    outside.write_bytes(path.read_bytes())
                    path.unlink()
                    path.symlink_to(outside)
                with self.assertRaises(ValueError):
                    normalize(INJECTED, root=root)

    def test_apptainer_fakeroot_trio_is_verified_but_not_exported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            injected, _ = self.fixture(root)
            sources = ('usr/bin/fakeroot-sysv', 'usr/bin/faked-sysv',
                       'usr/lib/x86_64-linux-gnu/libfakeroot/libfakeroot-sysv.so')
            for name, source in zip(FAKEROOT_FILES, sources):
                host = root / source
                host.parent.mkdir(parents=True, exist_ok=True)
                host.write_bytes(b'#!/bin/sh\n' if name == 'fakeroot' else b'\x7fELF' + name.encode())
                (injected / name).write_bytes(host.read_bytes())
            value, evidence = normalize(INJECTED, root=root)
            self.assertEqual(value, '/usr/lib/x86_64-linux-gnu')
            self.assertEqual(len(evidence['libraries']), 3)
            self.assertEqual(len(evidence['omitted_build_helpers']), 3)
            (injected / 'faked').write_bytes(b'\x7fELFmodified')
            with self.assertRaisesRegex(ValueError, 'helpers differ'):
                normalize(INJECTED, root=root)
            (injected / 'faked').unlink()
            with self.assertRaisesRegex(ValueError, 'incomplete injected fakeroot'):
                normalize(INJECTED, root=root)

    def test_incomplete_or_unknown_injection_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            injected, _ = self.fixture(root)
            (injected / REQUIRED[1]).unlink()
            with self.assertRaisesRegex(ValueError, 'lacks required'):
                normalize(INJECTED, root=root)
            for path in ('relative/lib', '/usr/../lib', '/.singularity.d/other', '/usr/lib\n'):
                with self.assertRaises(ValueError):
                    normalize(path, root=root)

    def test_all_injected_entries_must_have_matching_native_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            injected, native = self.fixture(root)
            extra = injected / 'libOpenGL.so.0'
            extra.write_bytes(b'\x7fELFgl')
            with self.assertRaisesRegex(ValueError, 'no native replacement'):
                normalize(INJECTED, root=root)
            (native / extra.name).write_bytes(extra.read_bytes())
            self.assertEqual(len(normalize(INJECTED, root=root)[1]['libraries']), 4)

    def test_required_guard_precedes_backend_probe_and_build(self):
        recipe = (ROOT / 'controller/md_build.sh').read_text()
        self.assertLess(recipe.index('/control/md_driver_paths.py'), recipe.index('/control/md_probe.py'))
        self.assertLess(recipe.index('/control/md_driver_paths.py'), recipe.index('cmake --build'))
        entry = (ROOT / 'controller/md_container_entry.sh').read_text()
        self.assertLess(entry.index('/control/md_driver_paths.py'), entry.index('git init'))

    def test_replacement_directories_cannot_shadow_each_other(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            injected, native = self.fixture(root)
            vdpau = native / 'vdpau'
            vdpau.mkdir()
            (injected / 'libA.so').write_bytes(b'\x7fELFA')
            (vdpau / 'libA.so').write_bytes(b'\x7fELFA')
            # libA sorts first and requires the subdirectory. That directory
            # must not silently shadow another driver supplied by the parent.
            (vdpau / REQUIRED[0]).write_bytes(b'\x7fELFwrong')
            with self.assertRaisesRegex(ValueError, 'changes library precedence'):
                normalize(INJECTED, root=root)

    def test_unknown_injected_files_and_broken_links_are_rejected(self):
        for name, broken in (('unexpected.txt', False), ('libbroken.so', True)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                injected, _ = self.fixture(root)
                if broken:
                    (injected / name).symlink_to('missing.so')
                else:
                    (injected / name).write_text('unexpected content')
                with self.assertRaises((ValueError, FileNotFoundError)):
                    normalize(INJECTED, root=root)

    def test_real_elf_loader_uses_same_library_after_normalization(self):
        compiler = shutil.which('cc')
        if not compiler:
            self.skipTest('C compiler required for real loader regression')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            injected, native = self.fixture(root)
            source = root / 'driver.c'
            source.write_text('int driver_value(void) { return 42; }\n')
            for name in REQUIRED:
                subprocess.run([compiler, '-shared', '-fPIC', str(source), '-Wl,-soname,' + name,
                                '-o', str(native / name)], check=True, capture_output=True)
                shutil.copyfile(native / name, injected / name)
            value, _ = normalize(INJECTED, root=root)
            self.assertEqual(value, '/usr/lib/x86_64-linux-gnu')
            probe = 'import ctypes; assert ctypes.CDLL("libcuda.so.1").driver_value() == 42'
            import os
            for path in (injected, native):
                result = subprocess.run([sys.executable, '-c', probe],
                                        env={**os.environ, 'LD_LIBRARY_PATH': str(path)},
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            # Byte mismatch is caught before another allocation's expensive build.
            (native / REQUIRED[0]).write_bytes(b'\x7fELFchanged')
            with self.assertRaisesRegex(ValueError, 'bytes differ'):
                normalize(INJECTED, root=root)


if __name__ == '__main__':
    unittest.main()
