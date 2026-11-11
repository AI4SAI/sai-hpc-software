import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
import install
base = Path(__file__).resolve().parent
install.PACKAGE = Path('/home/stardust/sai-hpc-software/deployment/gpumdkit-1.5.9')
root = base / 'native-stage'
root.mkdir()
os.umask(0o077)
normalize = install.normalize_staged_permissions
interrupted = {}
def stop_before_publish(payload, records):
    normalize(payload, records)
    interrupted['payload'] = str(payload)
    (payload / 'include/curses.h').chmod(0o600)
    raise OSError('intentional test interruption before publication')
try:
    with patch.object(install, 'normalize_staged_permissions', stop_before_publish):
        install.install(root)
except OSError as error:
    assert str(error) == 'intentional test interruption before publication', error
    print('EXPECTED_TEST_INTERRUPTION', error, flush=True)
else:
    raise AssertionError('interruption was not exercised')
assert not (root / install.PREFIX.relative_to('/')).exists()
assert not (root / install.MODULE.relative_to('/')).exists()
parent = root / install.PREFIX.relative_to('/').parent
stage, = parent.glob('.1.5.9-incoming-*')
install.install(root, stage)
assert not stage.exists()
install.install(root)
install.preflight(installed=True, root=root)
proof=dict(passed=True, route='fresh-extraction-and-resumed-native-trial', prefix=str(root / install.PREFIX.relative_to('/')),
           umask='0077', interrupted_before_publication=True, reused_existing_extraction=True,
           idempotent_install=True, stage_removed=True,
           installer_sha256=hashlib.sha256((base/'install.py').read_bytes()).hexdigest())
(base / 'native-install.json').write_text(json.dumps(proof,indent=2)+'\n')
print(json.dumps(proof,indent=2),flush=True)
