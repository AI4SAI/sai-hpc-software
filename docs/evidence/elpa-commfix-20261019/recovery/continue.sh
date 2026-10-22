#!/usr/bin/env bash
# Continue the exact interrupted 8V100V0 dependency graph in a checked clone.
set -euo pipefail
identity=${1:?}; lock=${2:?}
export INSTALL_PREFIX=$(python3 /control/delivery_layout.py prefix "$identity" cp2k \
  67b5da876dd6a76b8b021d5a04d1c81ba79a4c50 v2026.2 8v100v0-avx512)
export TMPDIR=/workspace/tmp
source /control/environment.sh 8v100v0-avx512
set +u
module unload gcc
module load gcc/14.3.0
set -u
export SPACK_DISABLE_LOCAL_CONFIG=1 SPACK_USER_CONFIG_PATH=/workspace/spack-config
export SPACK_USER_CACHE_PATH=/workspace/spack-cache PYTHONPATH=/workspace/solver:/control
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
printf '%s  %s\n' "$lock" /workspace/env/spack.lock | sha256sum --check --status
python3 /control/cp2k_spack_native.py prepare 8V100V0
spack_run() { /usr/bin/python3 /workspace/spack/bin/spack "$@"; }
spack_run -e /workspace/env python /control/cp2k_spack_native.py fetch \
  8V100V0 "$(spack_run arch -t)" "$(spack_run arch -o)" "$INSTALL_PREFIX"
# The existing graph, source patch and completed installs are retained. Do not
# reconcretize, reapply the Libint patch, or restage generated/compiled sources.
spack_run -e /workspace/env find --format '{name} /{hash:7} {prefix}' > /results/resumed-specs.txt
spack_run -e /workspace/env install --only-concrete --no-cache --fail-fast \
  --show-log-on-error --keep-stage --dont-restage -j "${BUILD_JOBS:?}"
printf '%s  %s\n' "$lock" /workspace/env/spack.lock | sha256sum --check --status
spack_run -e /workspace/env python /control/cp2k_spack_install_evidence.py 8V100V0 "$INSTALL_PREFIX"
printf '%s\n' "$identity" > /results/stage-identity.json
echo CP2K_RECOVERED_DEPENDENCIES_COMPLETE
