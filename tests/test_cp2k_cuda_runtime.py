"""Exercise CP2K's actual launcher shell with real competing shared libraries."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('cc'), 'a C compiler is required for real loader tests')
class CP2KRuntimeLoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workspace = ROOT / '.test-work'
        workspace.mkdir(exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix='cp2k-loader-', dir=workspace)
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.compat, cls.driver = cls.root / 'saved compat', cls.root / 'host driver'
        for directory in (cls.compat, cls.driver):
            directory.mkdir()
        for directory, name, value in ((cls.compat, 'libcp2k_loader_probe.so', 575),
                                       (cls.driver, 'libcp2k_loader_probe.so', 535),
                                       (cls.driver, 'libcp2k_driver_fallback.so', 123)):
            subprocess.run([shutil.which('cc'), '-shared', '-fPIC', '-x', 'c', '-',
                            '-Wl,-soname,' + name, '-o', str(directory / name)],
                           input=f'int loader_value(void) {{ return {value}; }}\n',
                           text=True, check=True, capture_output=True)
        cls.environment_file = cls.root / 'runtime-env.sh'
        cls.environment_file.write_text('export LD_LIBRARY_PATH=' + shlex.quote(str(cls.compat)) + '\n')
        # Parse the production command, retaining its exact shell code and
        # bash options. Only the container/image and scientific binary are
        # replaced by this local fixture.
        launcher = (ROOT / 'controller/cp2k_runtime.sh').read_text()
        final = launcher[launcher.rindex('"${args[@]}" "$image"'):].replace('\\\n', '')
        words = shlex.split(final)
        start = words.index('/usr/bin/bash')
        code = words.index('-c', start) + 1
        cls.command = words[start:code + 1]
        cls.consumer = (
            'import ctypes,json,os,sys; '
            'library=ctypes.CDLL(sys.argv[1]); '
            'print(json.dumps({"value":library.loader_value(),'
            '"paths":os.environ["LD_LIBRARY_PATH"].split(":")}))'
        )

    def launch(self, library, initial):
        environment = dict(os.environ)
        environment.pop('LD_PRELOAD', None)
        if initial is None:
            environment.pop('LD_LIBRARY_PATH', None)
        else:
            environment['LD_LIBRARY_PATH'] = initial
        result = subprocess.run([*self.command, 'bash', str(self.environment_file),
                                 sys.executable, '-c', self.consumer, library],
                                check=True, text=True, capture_output=True, env=environment)
        return json.loads(result.stdout)

    def test_saved_compat_library_wins_over_same_soname_host_driver(self):
        result = self.launch('libcp2k_loader_probe.so', str(self.driver))
        self.assertEqual(result['value'], 575)
        self.assertEqual(result['paths'], [str(self.compat), str(self.driver)])

    def test_library_absent_from_saved_environment_uses_driver_fallback(self):
        result = self.launch('libcp2k_driver_fallback.so', str(self.driver))
        self.assertEqual(result['value'], 123)
        self.assertEqual(result['paths'], [str(self.compat), str(self.driver)])

    def test_empty_or_unset_initial_path_has_no_current_directory_search(self):
        for initial in ('', None):
            with self.subTest(initial=initial):
                result = self.launch('libcp2k_loader_probe.so', initial)
                self.assertEqual(result['value'], 575)
                self.assertEqual(result['paths'], [str(self.compat)])


if __name__ == '__main__':
    unittest.main()
