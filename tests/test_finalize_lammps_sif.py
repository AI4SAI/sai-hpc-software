import importlib.util
from pathlib import Path
import subprocess
import unittest

SPEC = importlib.util.spec_from_file_location('finalize', Path(__file__).parents[1] / 'controller/finalize_lammps_sif.py')
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class FinalizeTests(unittest.TestCase):
    def test_readonly_extraction_before_verify(self):
        script = mod.render(Path('/new/run'), Path('/old/run'), ['apptainer', 'exec', '/new/run/result.sif', 'verify'])
        subprocess.run(['bash', '-n'], input=script, text=True, check=True)
        self.assertNotIn('--overlay', script)
        self.assertNotIn('debugfs -w', script)
        self.assertIn('/usr/sbin/debugfs -R', script)
        self.assertLess(script.index('dump /upper/workspace'), script.index('unsquashfs -s'))
        self.assertLess(script.index('unsquashfs -s'), script.index('apptainer sif new'))
        self.assertLess(script.index('apptainer sif add'), script.index('apptainer exec'))
        self.assertNotIn('cmake --build', script)

    def test_path_quote(self):
        self.assertEqual(mod.q(Path('/a path/file')), "'/a path/file'")


if __name__ == '__main__':
    unittest.main()
