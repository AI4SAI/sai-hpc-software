"""Apply the native acceptance guards to the pinned beta13 installation."""
from pathlib import Path
import sys
from unittest.mock import patch

import test_abacus_deployment_native as baseline

PACKAGE = Path(__file__).resolve().parents[1] / 'deployment/abacus-3.11.0-beta13'
with patch.dict(sys.modules, install=baseline.load('abacus_beta13_install_native', PACKAGE / 'install.py')):
    native = baseline.load('abacus_beta13_native', PACKAGE / 'native_checks.py')


class AbacusBeta13NativeTests(baseline.AbacusNativeChecksTests):
    def setUp(self):
        for name, value in (('native', native), ('PACKAGE', PACKAGE)):
            binding = patch.object(baseline, name, value)
            binding.start()
            self.addCleanup(binding.stop)
        super().setUp()
