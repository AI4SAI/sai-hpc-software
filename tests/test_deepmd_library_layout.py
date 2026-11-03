"""Installed monolithic libraries must actually define each backend API."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
from deepmd_standalone_check import inventory


@unittest.skipUnless(shutil.which('g++') and shutil.which('nm'), 'ELF toolchain needed')
class MonolithicLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.prefix = Path(self.temporary.name)
        (self.prefix / 'lib/python3.13/site-packages/deepmd/lib').mkdir(parents=True)
        (self.prefix / 'lib/libdeepmd_c.so').write_bytes(b'C API fixture')

    def build(self, defined, undefined=()):
        code = 'namespace deepmd {\n'
        for name in (*defined, *undefined):
            code += f'class DeepPot{name} {{ public: void init(); }};\n'
        for name in defined:
            code += f'void DeepPot{name}::init() {{}}\n'
        for name in undefined:
            code += f'void use{name}() {{ DeepPot{name} value; value.init(); }}\n'
        code += '}\n'
        library = self.prefix / 'lib/libdeepmd_cc.so'
        subprocess.run(['g++', '-shared', '-fPIC', '-x', 'c++', '-', '-o', str(library)],
                       input=code, text=True, check=True, capture_output=True)
        return library

    def inventory(self):
        with patch('deepmd_standalone_check.libraries', return_value='resolved'), \
                patch('deepmd_standalone_check.importlib.metadata.version', return_value='3.2.0b0'):
            return inventory(self.prefix)

    def test_old_monolithic_soname_and_new_plugin_layouts(self):
        library = self.build(('TF', 'PT', 'JAX', 'PTExpt'))
        versioned = library.with_name('libdeepmd_cc.so.3')
        library.rename(versioned)
        library.symlink_to(versioned.name)
        result = self.inventory()
        self.assertEqual(result['backends'], ['jax', 'pt', 'ptexpt', 'tf'])
        self.assertEqual(result['backend_layout'], 'monolithic')
        self.assertEqual(result['libraries'][str(library)]['resolved'], str(versioned))
        for name in ('tf', 'pt', 'ptexpt', 'jax'):
            (self.prefix / ('lib/libdeepmd_backend_' + name + '.so')).write_bytes(b'plugin')
        result = self.inventory()
        self.assertEqual(result['backend_layout'], 'plugins')
        self.assertEqual(result['backends'], ['jax', 'pt', 'ptexpt', 'tf'])

    def test_optional_backends_are_reported_only_when_their_native_api_is_defined(self):
        self.build(('TF', 'PT', 'JAX', 'PD'), ('PTExpt',))
        result = self.inventory()
        self.assertEqual(result['backends'], ['jax', 'pd', 'pt', 'tf'])
        self.assertNotIn('ptexpt', result['backend_evidence'])

    def test_undefined_backend_or_python_only_library_cannot_fill_a_native_gap(self):
        library = self.build(('TF', 'PT'), ('JAX',))
        with self.assertRaisesRegex(ValueError, 'backend feature regression'):
            self.inventory()
        library = self.build(('TF', 'PT', 'JAX'))
        library.rename(self.prefix / 'lib/python3.13/site-packages/deepmd/lib/libdeepmd_cc.so')
        with self.assertRaisesRegex(ValueError, 'backend feature regression'):
            self.inventory()

    def test_partial_plugin_layout_cannot_fall_back_to_a_monolithic_library(self):
        self.build(('TF', 'PT', 'JAX'))
        (self.prefix / 'lib/libdeepmd_backend_tf.so').write_bytes(b'plugin')
        with self.assertRaisesRegex(ValueError, 'backend feature regression'):
            self.inventory()
