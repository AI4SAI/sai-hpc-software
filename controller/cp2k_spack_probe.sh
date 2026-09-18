#!/usr/bin/env bash
# Native-node solver/fetch probe only: no CP2K or dependency installation.
set -euo pipefail
partition=${1:?partition required}
case "$partition" in
  16V100) target=16v100-avx2 ;;
  DSPRHBM) target=dsprhbm ;;
  *) echo 'Probe only the first two acceptance partitions' >&2; exit 2 ;;
esac
mkdir -p /workspace/tmp /workspace/spack /workspace/spack-packages /workspace/solver /workspace/env
export TMPDIR=/workspace/tmp
source /control/environment.sh "$target"
set +u
module load gcc/13.3.0
set -u
export SPACK_DISABLE_LOCAL_CONFIG=1
export SPACK_USER_CONFIG_PATH=/workspace/spack-config
export SPACK_USER_CACHE_PATH=/workspace/spack-cache
export PYTHONPATH=/workspace/solver:/control
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
python3 /control/cp2k_spack_native.py prepare "$partition"
date -u '+CP2K_SPACK_PROFILE_READY %FT%TZ'
if [[ -f /workspace/spack-bootstrap.json ]]; then
  python3 -c 'import json; from cp2k_spack_seed import manifest; assert json.load(open("/workspace/spack-bootstrap.json")) == manifest()'
  echo 'Using verified prepopulated source/solver overlay (no compiled dependencies)'
else
  tar --no-same-owner -xzf /input/spack/sources/spack.tar.gz -C /workspace/spack --strip-components=1
  tar --no-same-owner -xzf /input/spack/sources/packages.tar.gz -C /workspace/spack-packages --strip-components=1
  for wheel in /input/spack/sources/solver-wheels/*.whl; do
    python3 -m zipfile -e "$wheel" /workspace/solver
  done
fi
date -u '+CP2K_SPACK_BOOTSTRAP_READY %FT%TZ'
python3 -c 'import clingo; assert clingo.__version__ == "5.7.1"; print("Offline clingo", clingo.__version__)'
spack_run() { /usr/bin/python3 /workspace/spack/bin/spack "$@"; }
native_target=$(spack_run arch -t)
native_os=$(spack_run arch -o)
prefix="/opt/software/cp2k/development/spack-native-probe/$partition"
python3 /control/cp2k_spack_environment.py "$partition" --install-prefix "$prefix" \
  --native-target "$native_target" --native-os "$native_os" --output /workspace/env/spack.yaml
cp /workspace/env/spack.yaml /results/spack.yaml
date -u '+CP2K_SPACK_CONCRETIZE_START %FT%TZ'
spack_run -e /workspace/env concretize
cp /workspace/env/spack.lock /results/spack.lock
spack_run -e /workspace/env python /control/cp2k_spack_native.py fetch "$partition" "$native_target" "$native_os"
printf 'CP2K_SPACK_NATIVE_PROBE_COMPLETE %s\n' "$partition"
