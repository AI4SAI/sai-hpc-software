#!/usr/bin/env bash
# Evaluated inside the container. Dependency absolute paths match the host.
set -eo pipefail
target=${1:?target required}
set +u
export PATH=/usr/bin:/bin
export LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-} LD_PRELOAD=${LD_PRELOAD:-}
export CPATH=${CPATH:-} CMAKE_PREFIX_PATH=${CMAKE_PREFIX_PATH:-}
export USER=${USER:-root} LOGNAME=${LOGNAME:-root}
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/devtools
module load cmake/3.31.6 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto
module load fftw/3.3.10 libxc/7.0.0-auto saiblas/2603-gnu-auto elpa/2026.02.001-2603-gnu
[[ "${ELPA_ROOT:-}" == /opt/devtools/elpa/elpa-2026.02.001-2603-gnu/nvidia ]] || {
    echo "required system ELPA 2026.02.001 module did not resolve" >&2
    return 2
}
module load cuda/12.9.1
if [[ "$target" != dsprhbm ]]; then
    module load nvmplibs/26.7-tmp
fi
if [[ "$target" == dsprhbm ]]; then
    # The system compiler varies by node generation; the devtools module gcc
    # provides one uniform toolchain across CPU-only partitions.
    module load gcc/13.3.0
fi
export CMAKE_LIBRARY_PATH=${LIBRARY_PATH:-} CMAKE_INCLUDE_PATH=${CPATH:-}
case "$target" in
  4v100-avx512)
    grep -qw avx512_vnni /proc/cpuinfo
    [[ "${MPI_HOME:-}" == *-avx512 && "${OPENBLAS_ROOT:-}" == *-avx512 ]]
    ;;
  16v100-avx2)
    grep -qw avx2 /proc/cpuinfo
    ! grep -qw avx512f /proc/cpuinfo
    [[ "${MPI_HOME:-}" == *-avx2 && "${OPENBLAS_ROOT:-}" == *-avx2 ]]
    ;;
  dsprhbm)
    grep -qw avx512f /proc/cpuinfo
    ;;
  a100) ;;
  *) echo "unknown build target: $target" >&2; return 2 ;;
esac
set -u
