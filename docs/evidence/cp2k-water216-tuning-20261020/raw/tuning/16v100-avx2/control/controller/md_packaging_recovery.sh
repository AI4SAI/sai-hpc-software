#!/usr/bin/env bash
# Finalize an already compiled LAMMPS/DeepMD overlay. No configure/build step.
set -euo pipefail
[[ "${1:-}" == package && -n "${2:-}" ]]
export SAI_MD_DELIVERY=$2
settings=$(/usr/bin/python3 - <<'PY'
import json, os, sys
sys.path.insert(0, '/control')
from md_controller import validate_pair
r = validate_pair(json.loads(os.environ['SAI_MD_DELIVERY']))
print(r['plan']['target'])
for name in ('deepmd-kit', 'lammps'):
    print(r['identities'][name]['install_prefix'])
PY
)
mapfile -t settings <<< "$settings"
export DEEPMD_PREFIX=${settings[1]} LAMMPS_PREFIX=${settings[2]}
export PYTHONDONTWRITEBYTECODE=1 TMPDIR=/runtime
source /control/md_environment.sh "${settings[0]}"
test -s "$LAMMPS_PREFIX/lib/liblammps.so"
runtime="$LAMMPS_PREFIX/share/sai/runtime-env.sh"
test -s "$runtime"
source "$runtime"
# The image exposes /bin and /sbin as aliases of the host base system. They
# are not explicit site dependency prefixes and must not enter native manifests.
for name in PATH LD_LIBRARY_PATH PYTHONPATH; do
  IFS=: read -ra components <<< "${!name:-}"
  cleaned=()
  for component in "${components[@]}"; do
    [[ -n "$component" && "$component" != /bin && "$component" != /sbin ]] || continue
    cleaned+=("$component")
  done
  printf -v "$name" '%s' "$(IFS=:; printf '%s' "${cleaned[*]}")"
  export "$name"
done
for name in PATH LD_LIBRARY_PATH PYTHONPATH PLUMED_KERNEL LAMMPS_POTENTIALS; do
  printf 'export %s=%q\n' "$name" "${!name}"
done > "$runtime"
"$DEEPMD_PREFIX/bin/python" - <<'PY'
import json, os, pathlib, sys
sys.path.insert(0, '/control')
from md_controller import validate_pair
from export_native import write_manifests
from native_module import _external_root
from release_contract import SOFTWARE
delivery = validate_pair(json.loads(os.environ['SAI_MD_DELIVERY']))
identities = delivery['identities']
prefixes = [item['install_prefix'] for item in identities.values()]
runtime = {
    'modules': [x for x in os.environ.get('LOADEDMODULES', '').split(':')
                if x and x.split('/')[0] not in {*SOFTWARE, 'apptainer', 'cmake'}],
    'prepend': {},
    'set': {name: os.environ[name] for name in ('PLUMED_KERNEL', 'LAMMPS_POTENTIALS')},
}
external = {'/usr', '/lib', '/lib64', os.environ['MD_SYSTEM_DEEPMD'], os.environ['MD_SYSTEM_PLUMED']}
for name in ('PATH', 'LD_LIBRARY_PATH', 'PYTHONPATH', 'MODULEPATH'):
    kept = []
    for raw in os.environ.get(name, '').split(':'):
        if not raw:
            continue
        resolved = str(pathlib.Path(raw).resolve(strict=True))
        if resolved in ('/bin', '/sbin'):
            continue
        if any(pathlib.PurePosixPath(resolved).is_relative_to(prefix) for prefix in prefixes):
            kept.append(resolved)
            continue
        if resolved.startswith('/opt/apps/lammps/') or resolved == '/opt/apps/lammps':
            raise ValueError('old LAMMPS runtime path leaked into candidate')
        try:
            _external_root(resolved)
        except ValueError:
            raise ValueError(f'unrecorded runtime path: {name}={resolved}')
        external.add(resolved)
        kept.append(resolved)
    runtime['prepend'][name] = list(dict.fromkeys(kept))
entries = [{'identity': identities[software], 'commands': {command: 'bin/' + command},
            'runtime': runtime, 'external_roots': sorted(external)}
           for software, command in (('deepmd-kit', 'dp'), ('lammps', 'lmp'))]
write_manifests(entries)
print('MD_MANIFESTS_PACKAGED')
PY
/usr/bin/bash /control/md_container_entry.sh export "$SAI_MD_DELIVERY"
echo MD_PACKAGING_RECOVERY_COMPLETE
