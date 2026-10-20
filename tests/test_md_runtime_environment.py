"""Baseline startup must find its own backend plugins and honor the test's threads."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


class BaselineEnvironmentTests(unittest.TestCase):
    def test_site_module_thread_override_and_wheel_plugin_directory(self):
        script = (Path(__file__).resolve().parents[1] / 'controller/md_runtime.sh').read_text()
        line = next(line.strip() for line in script.splitlines()
                    if 'source /control/md_environment.sh "$1"' in line)
        command = shlex.split(line.removesuffix(' \\'))[0]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            environment = directory / 'environment.sh'
            prefix = directory / 'site-deepmd'
            environment.write_text('export OMP_NUM_THREADS=2\nexport MD_SYSTEM_DEEPMD=' + shlex.quote(str(prefix)) + '\n')
            command = command.replace('/control/md_environment.sh', shlex.quote(str(environment)))
            result = subprocess.run(['/usr/bin/bash', '--noprofile', '--norc', '-c', command, 'bash', '16v100-avx2',
                                     '/usr/bin/python3', '-c',
                                     'import json,os; print(json.dumps({k:os.environ[k] for k in '
                                     '("OMP_NUM_THREADS","DP_BACKEND_PLUGIN_PATH","LD_LIBRARY_PATH")}))'],
                                    env=dict(os.environ, OMP_NUM_THREADS='1', LD_LIBRARY_PATH='/native-driver'),
                                    check=True, text=True, capture_output=True)
            observed = json.loads(result.stdout)
            plugins = str(prefix / 'lib/python3.13/site-packages/deepmd/lib')
            self.assertEqual(observed['OMP_NUM_THREADS'], '1')
            self.assertEqual(observed['DP_BACKEND_PLUGIN_PATH'], plugins)
            self.assertIn(plugins, observed['LD_LIBRARY_PATH'].split(':'))
            self.assertEqual(observed['LD_LIBRARY_PATH'].split(':')[0], '/native-driver')
