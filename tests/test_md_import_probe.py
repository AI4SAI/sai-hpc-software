"""Import-only diagnostics never count as scientific acceptance."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'controller/md_import_probe.sh'


class ImportProbeTests(unittest.TestCase):
    def test_syntax_allocation_and_cpu_isolation(self):
        subprocess.run(['bash', '-n', str(SCRIPT)], check=True)
        text = SCRIPT.read_text()
        self.assertIn('SAI_MD_ALLOCATED_NODE%%.*', text)
        self.assertIn('export CUDA_VISIBLE_DEVICES=\'\' PYTHONFAULTHANDLER=1', text)
        self.assertIn('ulimit -c 0', text)
        self.assertIn('timeout=40', text)
        self.assertIn("('torch', 'tensorflow', 'triton')", text)
        self.assertIn("('tensorflow', 'torch', 'triton')", text)
        self.assertIn("'scientific_verified': False", text)
        self.assertIn("'candidate_verified': False", text)
        self.assertNotIn('git clone', text)
        self.assertNotIn('pip install', text)
        self.assertNotIn('sbatch', text)
        self.assertLess(text.index('require_execution_context()'), text.index('subprocess.run(command'))

    def test_host_or_bad_arguments_fail_before_site_environment_load(self):
        for args in ([], ['wrong'], ['4v100-avx512'], ['4v100-avx512', 'extra']):
            result = subprocess.run(['bash', str(SCRIPT), *args], env={'PATH': '/usr/bin:/bin'},
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn('/control/md_environment.sh', result.stderr)


if __name__ == '__main__':
    unittest.main()
