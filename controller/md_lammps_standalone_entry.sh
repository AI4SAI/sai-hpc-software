#!/usr/bin/env bash
# Only the new LAMMPS prefix is writable. Installed DeePMD is bound read-only.
set -euo pipefail
phase=$1
export SAI_LAMMPS_DELIVERY=$2
settings=$(/usr/bin/python3 - <<'PY'
import json, os, sys
sys.path.insert(0, '/control')
from md_lammps_standalone import dependency, validate_delivery, verify_dependency
delivery = validate_delivery(json.loads(os.environ['SAI_LAMMPS_DELIVERY']))
identity = delivery['identity']
dep, files = dependency(identity['target'])
verify_dependency(dep, files)
for value in (identity['target'], identity['source_sha'], identity['install_prefix'],
              dep['identity']['source_sha'], dep['identity']['install_prefix']):
    print(value)
PY
)
mapfile -t settings <<< "$settings"
target=${settings[0]}; lmp_sha=${settings[1]}; dp_sha=${settings[3]}
export LAMMPS_PREFIX=${settings[2]} DEEPMD_PREFIX=${settings[4]}
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PYTHONDONTWRITEBYTECODE=1
case "$phase" in
  build)
    export TMPDIR=/workspace/tmp
    mkdir -p "$TMPDIR"
    /usr/bin/python3 - <<'PY'
import os
cpus = sorted(os.sched_getaffinity(0))
if len(cpus) < int(os.environ['BUILD_JOBS']):
    raise ValueError('container CPU affinity cannot supply requested compilation workers')
print('LAMMPS_BUILD_AFFINITY', len(cpus), cpus, flush=True)
PY
    for name in lammps deepmd-kit; do
      if [[ "$name" == lammps ]]; then sha=$lmp_sha; else sha=$dp_sha; fi
      git init "/workspace/$name"
      git -C "/workspace/$name" -c core.hooksPath=/dev/null fetch --depth=1 --no-tags "/input/$name" "$sha"
      git -C "/workspace/$name" -c core.hooksPath=/dev/null checkout --detach "$sha"
      [[ "$(git -C "/workspace/$name" rev-parse HEAD)" == "$sha" ]]
    done
    # The DeePMD checkout supplies matching LAMMPS interface sources and fixtures
    # only. Its Python package and C/C++ libraries are never rebuilt or copied.
    /usr/bin/python3 - <<'PY'
import json, os, sys
sys.path.insert(0, '/control')
from md_source_version import restore_release_tag
identity = json.loads(os.environ['SAI_LAMMPS_DELIVERY'])['identity']
restore_release_tag('/workspace/lammps', dict(sha=identity['source_sha'],
                    track=identity['track'], ref=identity['source_ref']))
PY
    bash /control/md_build.sh "$target" lammps-installed
    ;;
  export)
    bash /control/create_rootfs.sh /workspace/export
    mkdir -p "/workspace/export$(dirname -- "$LAMMPS_PREFIX")"
    cp -a "$LAMMPS_PREFIX" "/workspace/export$LAMMPS_PREFIX"
    env -u LD_PRELOAD /usr/bin/python3 - <<'PY'
import json, os, sys
sys.path.insert(0, '/control')
from md_packaging import prepare
identity = json.loads(os.environ['SAI_LAMMPS_DELIVERY'])['identity']
print(prepare('/workspace/export', [identity]))
PY
    env -u LD_PRELOAD mksquashfs /workspace/export /workspace/final.squashfs \
      -noappend -all-root -no-xattrs -processors "$BUILD_JOBS"
    ;;
  verify)
    export TMPDIR=/runtime
    source "$LAMMPS_PREFIX/share/sai/runtime-env.sh"
    "$LAMMPS_PREFIX/bin/lmp" -h
    "$DEEPMD_PREFIX/bin/python" /control/md_relocate_audit.py "$DEEPMD_PREFIX" "$LAMMPS_PREFIX"
    /usr/bin/python3 - <<'PY'
import json, os, sys
sys.path.insert(0, '/control')
from pathlib import Path
from export_native import MANIFEST_PATH, inventory, read_installed_manifests
identity = json.loads(os.environ['SAI_LAMMPS_DELIVERY'])['identity']
entry = json.loads((Path(identity['install_prefix']) / MANIFEST_PATH).read_text())['entry']
if entry['identity'] != identity or read_installed_manifests([entry]) != inventory([entry]):
    raise ValueError('standalone SIF identity or installed files changed')
print('LAMMPS_STANDALONE_SIF_INVENTORY_VERIFIED')
PY
    ;;
  *) exit 2 ;;
esac
