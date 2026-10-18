"""Exercise build-alias removal on real loadable ELF libraries."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'controller'))
from cp2k_elpa_runtime import is_hcoll_build_path, remove_build_alias, runpaths


class HCOLLPathTests(unittest.TestCase):
    def test_alias_is_detected_lexically_without_relying_on_host_symlinks(self):
        for path in ('/opt/mellanox/hcoll', '/opt/mellanox/hcoll/lib', '/opt/mellanox/./hcoll/lib',
                     '/opt/mellanox/hcoll/lib/../lib64'):
            self.assertTrue(is_hcoll_build_path(path))
        for path in ('/opt/devtools/hpcx/hcoll/lib', '/opt/mellanox/hcoll-other/lib', '$ORIGIN'):
            self.assertFalse(is_hcoll_build_path(path))


@unittest.skipUnless(all(shutil.which(tool) for tool in ('gcc', 'readelf', 'chrpath')),
                     'real ELF runtime tests require gcc, readelf and chrpath')
class ELPAELFRuntimeTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / '.test-work'
        parent.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def library(self, rpath, old_dtags=False, dependency=False):
        path = self.root / 'libelpa_fixture.so'
        arguments = ['gcc', '-shared', '-fPIC', '-x', 'c', '-', '-o', str(path)]
        if dependency:
            directory = self.root / 'deps'
            directory.mkdir()
            subprocess.run(['gcc', '-shared', '-fPIC', '-x', 'c', '-',
                            '-Wl,-soname,libfixture_dependency.so',
                            '-o', str(directory / 'libfixture_dependency.so')],
                           input='int dependency(void) { return 47; }', text=True, check=True, capture_output=True)
            source = 'extern int dependency(void); int answer(void) { return dependency(); }'
            arguments += ['-L' + str(directory), '-lfixture_dependency']
        else:
            source = 'int answer(void) { return 47; }'
        arguments += ['-Wl,--disable-new-dtags' if old_dtags else '-Wl,--enable-new-dtags',
                      '-Wl,-rpath,' + rpath]
        subprocess.run(arguments, input=source, text=True, check=True, capture_output=True)
        return path

    def assert_loadable(self, library):
        # A subprocess prevents an earlier dlopen from hiding broken paths.
        process = subprocess.run([sys.executable, '-c',
            'import ctypes,sys; assert ctypes.CDLL(sys.argv[1]).answer() == 47', str(library)],
            text=True, capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr)

    def test_keeps_origin_dependency_loading_and_loader_tag_kind(self):
        for old_dtags in (False, True):
            with self.subTest(old_dtags=old_dtags):
                directory = self.root / 'deps'
                if directory.exists():
                    shutil.rmtree(directory)
                library = self.library('/opt/mellanox/hcoll/lib:$ORIGIN/deps:/usr/lib',
                                       old_dtags=old_dtags, dependency=True)
                receipt = remove_build_alias(library)
                self.assertTrue(receipt['changed'])
                self.assertNotEqual(receipt['original_sha256'], receipt['installed_sha256'])
                self.assertEqual(runpaths(library), [{'tag': 'RPATH' if old_dtags else 'RUNPATH',
                                                     'value': '$ORIGIN/deps:/usr/lib'}])
                self.assert_loadable(library)

    def test_alias_only_path_is_removed_without_breaking_elf(self):
        library = self.library('/opt/mellanox/hcoll/lib')
        self.assertTrue(remove_build_alias(library)['changed'])
        self.assertEqual(runpaths(library), [])
        self.assert_loadable(library)

    def test_clean_library_is_byte_identical_and_cleanup_is_idempotent(self):
        library = self.library('$ORIGIN:/usr/lib')
        before = library.read_bytes()
        for _ in range(2):
            receipt = remove_build_alias(library)
            self.assertFalse(receipt['changed'])
            self.assertEqual(receipt['original_sha256'], receipt['installed_sha256'])
            self.assertEqual(before, library.read_bytes())
        self.assert_loadable(library)


if __name__ == '__main__':
    unittest.main()
