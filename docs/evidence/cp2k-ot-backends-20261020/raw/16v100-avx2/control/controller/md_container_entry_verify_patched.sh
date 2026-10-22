#!/usr/bin/env bash
# Verification-only copy used by the SIF finalizer.  The locked controller
# remains mounted at /control, so its recipe fingerprint is unchanged.  The
# only difference is that the metadata audit is supplied as a separately
# mounted diagnostic helper; this lets us fix an audit false positive without
# mutating the frozen build controller.
set -euo pipefail
phase=$1
export SAI_MD_DELIVERY=$2
settings=$(/usr/bin/python3 - <<'PY'
import json, os, sys
sys.path.insert(0, '/control')
from md_controller import validate_pair
from md_tracking import fingerprint
request = validate_pair(json.loads(os.environ['SAI_MD_DELIVERY']))
if request['recipe_sha256'] != fingerprint('/control'):
    raise ValueError('container recipe differs from locked identities')
print(request['plan']['target'])
for software in ('deepmd-kit', 'lammps'):
    print(request['identities'][software]['source_sha'])
    print(request['identities'][software]['install_prefix'])
PY
)
mapfile -t settings <<< "$settings"
target=${settings[0]}; dp_sha=${settings[1]}; lmp_sha=${settings[3]}
export DEEPMD_PREFIX=${settings[2]} LAMMPS_PREFIX=${settings[4]}
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin TMPDIR=/workspace/tmp
case "$phase" in
  verify)
    source "$LAMMPS_PREFIX/share/sai/runtime-env.sh"
    "$DEEPMD_PREFIX/bin/dp" --version
    "$LAMMPS_PREFIX/bin/lmp" -h
    "$DEEPMD_PREFIX/bin/python" /audit-patched.py "$DEEPMD_PREFIX" "$LAMMPS_PREFIX"
    /usr/bin/python3 - <<'PY'
import json, os, pathlib, sys
sys.path.insert(0, '/control')
from md_controller import validate_pair
from export_native import MANIFEST_PATH, read_installed_manifests, inventory
delivery = validate_pair(json.loads(os.environ['SAI_MD_DELIVERY']))
entries = []
for identity in delivery['identities'].values():
    local = json.loads((pathlib.Path(identity['install_prefix']) / MANIFEST_PATH).read_text())
    if local['entry']['identity'] != identity:
        raise ValueError('embedded native identity differs from locked delivery')
    entries.append(local['entry'])
if read_installed_manifests(entries) != inventory(entries):
    raise ValueError('installed native files differ from embedded inventory')
PY
    ;;
  *) echo 'verification wrapper only supports verify' >&2; exit 2 ;;
esac
