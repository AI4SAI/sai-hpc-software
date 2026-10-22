#!/usr/bin/env python3
"""Install the accepted shared-ELPA CP2K release and switch the public module."""
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parent
base_path = PACKAGE / 'base_install.py'
if not base_path.exists():
    base_path = PACKAGE.with_name('cp2k-2026.2') / 'install.py'
spec = importlib.util.spec_from_file_location('cp2k_deployment_base', base_path)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
base.PACKAGE = PACKAGE


def __getattr__(name):
    return getattr(base, name)


if __name__ == '__main__':
    try:
        release = json.loads((PACKAGE / 'release.json').read_text())
        base.require(base.checksum(base_path) == release['base_installer_sha256'],
                     'installer implementation differs from prepared package')
        base.main()
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit('Deployment stopped: ' + str(error)) from error
