#!/usr/bin/env bash
# System dependencies are reused read-only, at their native absolute paths.
set -eo pipefail
set +u
export PATH=/usr/bin:/bin LD_LIBRARY_PATH= LD_PRELOAD=
export USER=${USER:-root} LOGNAME=${LOGNAME:-root}
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/devtools /opt/modules/modulefiles/apps
module load cuda/12.9.1 deepmd-kit/3.2.0 gcc/13.3.0
# The site has no standalone PLUMED module. Do not load the LAMMPS application
# merely to pick up these libraries; record and bind its dependency explicitly.
export GPUMD_PLUMED_PREFIX=/opt/apps/plumed/plumed-2.10.1
export PLUMED_KERNEL=$GPUMD_PLUMED_PREFIX/lib/libplumedKernel.so
# Site PLUMED's SONAME dependencies traverse /etc/alternatives on the host.
# Resolve to the actual read-only /usr library directories in the minimal SIF;
# do not copy the host alternatives database or substitute another BLAS ABI.
export GPUMD_SYSTEM_BLAS_PATH=/usr/lib/x86_64-linux-gnu/blas:/usr/lib/x86_64-linux-gnu/lapack
export LD_LIBRARY_PATH="$GPUMD_SYSTEM_BLAS_PATH:$GPUMD_PLUMED_PREFIX/lib:$DEEPMD_ROOT/lib:${LD_LIBRARY_PATH:-}"
test -r "$DEEPMD_ROOT/include/deepmd/DeepPot.h"
test -r "$DEEPMD_ROOT/lib/libdeepmd_cc.so"
test -r "$GPUMD_PLUMED_PREFIX/include/plumed/wrapper/Plumed.h"
test -r "$PLUMED_KERNEL"
plumed_dependencies=$(ldd "$PLUMED_KERNEL")
printf '%s\n' "$plumed_dependencies"
if [[ "$plumed_dependencies" == *"not found"* ]]; then
  echo 'PLUMED transitive dependencies are incomplete inside this container' >&2
  return 2
fi
export GPUMD_CUDA_ARCH=70 GPUMD_CPU_ARCH=native
case "${1:?target required}" in
  4v100-avx512) export GPUMD_EXPECTED_CPU_ARCH=znver4; grep -qw avx512_vnni /proc/cpuinfo ;;
  16v100-avx2) export GPUMD_EXPECTED_CPU_ARCH=znver3; grep -qw avx2 /proc/cpuinfo; ! grep -qw avx512f /proc/cpuinfo ;;
  8v100v0-avx512) export GPUMD_EXPECTED_CPU_ARCH=skylake-avx512; grep -qw avx512f /proc/cpuinfo; ! grep -qw avx512_vnni /proc/cpuinfo ;;
  *) echo 'GPUMD requires a registered GPU target' >&2; return 2 ;;
esac
resolved_arch=
while read -r option value remainder; do
  if [[ "$option" == -march= ]]; then resolved_arch=$value; fi
done < <(gcc -march=native -Q --help=target)
[[ "$resolved_arch" == "$GPUMD_EXPECTED_CPU_ARCH" ]] || {
  echo "native compiler architecture mismatch: $resolved_arch != $GPUMD_EXPECTED_CPU_ARCH" >&2
  return 2
}
set -u
