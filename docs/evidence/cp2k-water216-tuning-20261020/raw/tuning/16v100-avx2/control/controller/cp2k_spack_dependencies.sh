#!/usr/bin/env bash
# A fresh dependency stage at the application's final prefix; no publication.
set -euo pipefail
partition=${1:?partition required}
prefix=${2:?final install prefix required}
python3 - "$partition" "$prefix" <<'PY'
import sys
sys.path.insert(0, '/control')
from cp2k_spack import validated_prefix
validated_prefix(sys.argv[1], sys.argv[2], app_only=True)
PY
for tool in rm cp mv mkdir ln; do
  [[ -x "/bin/$tool" ]]
  cmp -s "/bin/$tool" "/usr/bin/$tool"
done
# The probe initializes the exact module/compiler environment and offline
# source-only bootstrap; it never imports old installed dependencies.
source /control/cp2k_spack_probe.sh "$partition" "$prefix"
lock_hash=$(sha256sum /workspace/env/spack.lock)
lock_hash=${lock_hash%% *}
spack_run -e /workspace/env python /control/cp2k_spack_libint_repair.py --fresh-stage
date -u '+CP2K_FINAL_PREFIX_DEPENDENCIES_START %FT%TZ'
spack_run -e /workspace/env install --only-concrete --no-cache --fail-fast \
  --show-log-on-error --keep-stage --dont-restage -j "${BUILD_JOBS:-8}"
printf '%s  %s\n' "$lock_hash" /workspace/env/spack.lock | sha256sum --check --status
spack_run -e /workspace/env python /control/cp2k_spack_install_evidence.py "$partition" "$prefix"
date -u '+CP2K_FINAL_PREFIX_DEPENDENCIES_COMPLETE %FT%TZ'
