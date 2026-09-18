#!/usr/bin/env bash
# Dependency pilot only; does not build or publish CP2K itself.
set -euo pipefail
partition=${1:?partition required}
expected_lock=${2:?verified lock SHA256 required}
repair_mode=${3:-none}
[[ "$repair_mode" == none || "$repair_mode" == mpi-runtime || "$repair_mode" == bin-tools ]]
if [[ "$repair_mode" == bin-tools ]]; then
  for tool in rm cp mv mkdir ln; do
    [[ -x "/bin/$tool" ]]
    cmp -s "/bin/$tool" "/usr/bin/$tool"
  done
  echo 'READONLY_BIN_TOOLS_READY: native checkpoint, versions and features unchanged'
fi
case "$partition" in
  16V100) target=16v100-avx2 ;;
  DSPRHBM) target=dsprhbm ;;
  *) exit 2 ;;
esac
export TMPDIR=/workspace/tmp
source /control/environment.sh "$target"
set +u
module load gcc/13.3.0
set -u
export SPACK_DISABLE_LOCAL_CONFIG=1 SPACK_USER_CONFIG_PATH=/workspace/spack-config
export SPACK_USER_CACHE_PATH=/workspace/spack-cache PYTHONPATH=/workspace/solver:/control
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
printf '%s  %s\n' "$expected_lock" /workspace/env/spack.lock | sha256sum --check --status
python3 /control/cp2k_spack_native.py prepare "$partition"
spack_run() { /usr/bin/python3 /workspace/spack/bin/spack "$@"; }
native_target=$(spack_run arch -t)
native_os=$(spack_run arch -o)
if [[ "$repair_mode" == mpi-runtime ]]; then
  python3 /control/cp2k_spack_mpi_repair.py "$partition" "$native_target" "$native_os"
  spack_run -e /workspace/env concretize --force
  expected_lock=$(sha256sum /workspace/env/spack.lock)
  expected_lock=${expected_lock%% *}
  printf '%s\n' "$expected_lock" > /results/repaired-lock.sha256
fi
# Revalidate the native architecture, features and externals on the new node.
spack_run -e /workspace/env python /control/cp2k_spack_native.py fetch "$partition" "$native_target" "$native_os"
cp /workspace/env/spack.yaml /workspace/env/spack.lock /results/
date -u '+CP2K_SPACK_INSTALL_START %FT%TZ'
spack_run -e /workspace/env install --only-concrete --no-cache --fail-fast --show-log-on-error --keep-stage -j "${BUILD_JOBS:-8}"
printf '%s  %s\n' "$expected_lock" /workspace/env/spack.lock | sha256sum --check --status
spack_run -e /workspace/env python /control/cp2k_spack_install_evidence.py "$partition"
date -u '+CP2K_SPACK_INSTALL_COMPLETE %FT%TZ'
