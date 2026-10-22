#!/usr/bin/env bash
set -euxo pipefail
source /control/md_environment.sh 16v100-avx2
export DEEPMD_PREFIX=/opt/apps/deepmd-kit/deepmd-kit-development-master-2026-09-20-g1313650a7ecd-r64e5a0fa63cb-s8ee8c7ef2080/16V100
export LAMMPS_PREFIX=/opt/apps/lammps/lammps-development-develop-2026-09-15-gc8bd2ae5927e-r64e5a0fa63cb-s8ee8c7ef2080/16V100
source "$LAMMPS_PREFIX/share/sai/runtime-env.sh"
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
"$DEEPMD_PREFIX/bin/python" - <<'PY'
import os, pathlib, sys
sys.path.insert(0, '/control')
from native_module import _external_root
prefixes = [os.environ['DEEPMD_PREFIX'], os.environ['LAMMPS_PREFIX']]
for name in ('PATH', 'LD_LIBRARY_PATH', 'PYTHONPATH', 'MODULEPATH'):
    print('VARIABLE', name)
    for raw in os.environ.get(name, '').split(':'):
        if not raw:
            continue
        resolved = str(pathlib.Path(raw).resolve(strict=True))
        own = any(pathlib.PurePosixPath(resolved).is_relative_to(prefix) for prefix in prefixes)
        try:
            root = _external_root(resolved)
            verdict = 'external:' + str(root)
        except Exception as error:
            verdict = 'INVALID:' + repr(error)
        print(raw, '=>', resolved, 'own=', own, verdict, flush=True)
PY
echo MANIFEST_PATH_DIAGNOSE_COMPLETE
