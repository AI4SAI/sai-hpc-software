import importlib.util
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cp2k_export_recovery',
    REPO / 'docs/evidence/cp2k-export-recovery-20261020/submit.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class ExportRecoveryTests(unittest.TestCase):
    def test_frozen_commands_survive_stdin_reader_and_preserve_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            run = root / 'runs/recovery'
            run.mkdir(parents=True)
            capacity = REPO / 'controller/build_capacity.py'
            prefix = shlex.join([sys.executable, str(capacity), str(root), str(run),
                                 '16384', '--', '/bin/bash', '-seu', '-o', 'pipefail'])
            for fail in (False, True):
                body = 'cat >/dev/null\n' + ('false | true\n' if fail else '') + 'echo VERIFIED\n'
                frozen = prefix + " <<'SAI_BUILD_BODY'\n" + body + 'SAI_BUILD_BODY\n'
                fixed = recovery.isolate_script(frozen)
                subprocess.run(['bash', '-n'], input=fixed, text=True, check=True)
                result = subprocess.run(['bash'], input=fixed, text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode == 0, not fail, result.stderr)
                self.assertEqual('VERIFIED' in result.stdout, not fail)

    def test_unknown_frozen_wrapper_is_rejected(self):
        for text in ('echo done\n', "bash -s <<'SAI_BUILD_BODY'\necho done\nSAI_BUILD_BODY\n"):
            with self.assertRaises(ValueError):
                recovery.isolate_script(text)
