#!/usr/bin/env bash
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
export PATH=/usr/bin:/bin TMPDIR=/workspace/tmp
case "$phase" in
  build)
    mkdir -p /workspace/tmp
    for name in deepmd-kit lammps; do
      if [[ "$name" == deepmd-kit ]]; then sha=$dp_sha; else sha=$lmp_sha; fi
      git init "/workspace/$name"
      git -C "/workspace/$name" -c core.hooksPath=/dev/null fetch --depth=1 --no-tags "/input/$name" "$sha"
      git -C "/workspace/$name" -c core.hooksPath=/dev/null checkout --detach "$sha"
      [[ "$(git -C "/workspace/$name" rev-parse HEAD)" == "$sha" ]]
    done
    bash /control/md_build.sh "$target"
    ;;
  export)
    bash /control/create_rootfs.sh /workspace/export
    mkdir -p /workspace/export/opt/apps /workspace/export/opt/software
    cp -a /opt/software/deepmd-kit /opt/software/lammps /workspace/export/opt/software/
    mksquashfs /workspace/export /workspace/final.squashfs -noappend -all-root -no-xattrs -processors "$BUILD_JOBS"
    ;;
  verify)
    source "$LAMMPS_PREFIX/share/sai/runtime-env.sh"
    "$DEEPMD_PREFIX/bin/dp" --version
    "$LAMMPS_PREFIX/bin/lmp" -h
    "$DEEPMD_PREFIX/bin/python" /control/md_relocate_audit.py "$DEEPMD_PREFIX" "$LAMMPS_PREFIX"
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
  *) exit 2 ;;
esac
