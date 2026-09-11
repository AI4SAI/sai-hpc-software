#!/usr/bin/env bash
# Reuse the site's ABI-matched Python backends, MPI, CUDA and PLUMED read-only.
# ELPA is not a dependency of DeePMD or LAMMPS and must not be rebuilt here.
set -eo pipefail
set +u
export LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-} LD_PRELOAD=${LD_PRELOAD:-}
export CPATH=${CPATH:-} CMAKE_PREFIX_PATH=${CMAKE_PREFIX_PATH:-}
export USER=${USER:-root} LOGNAME=${LOGNAME:-root}
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/devtools /opt/modules/modulefiles/apps
module load cmake/3.31.6 cuda/12.9.1
module load openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto
module load lammps/4jul2026-deepmd320-plumed2101-nvhpc263-ompi5010-sm70
export MD_SYSTEM_DEEPMD=/opt/apps/conda_env/deepmd-kit-3.2.0
export MD_SYSTEM_LAMMPS=/opt/apps/lammps/lammps-4Jul2026-deepmd3.2.0-plumed2.10.1-nvhpc263-ompi5010-sm70
export MD_SYSTEM_PLUMED=/opt/apps/plumed/plumed-2.10.1
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1
export CC=gcc CXX=g++ FC=gfortran
export PLUMED_KERNEL="$MD_SYSTEM_PLUMED/lib/libplumedKernel.so"
export PKG_CONFIG_PATH="$MD_SYSTEM_PLUMED/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
export CMAKE_PREFIX_PATH="$MD_SYSTEM_PLUMED:$MD_SYSTEM_DEEPMD:${CMAKE_PREFIX_PATH:-}"
[[ -x "$MD_SYSTEM_DEEPMD/bin/python" && -f "$PLUMED_KERNEL" ]]
case "$1" in
  4v100-avx512)
    export MD_CPU_ARCH=znver4 MD_CUDA_ARCH=70
    grep -qw avx512_vnni /proc/cpuinfo
    [[ "${MPI_HOME:-}" == *-avx512 ]]
    ;;
  16v100-avx2)
    export MD_CPU_ARCH=znver3 MD_CUDA_ARCH=70
    grep -qw avx2 /proc/cpuinfo
    ! grep -qw avx512f /proc/cpuinfo
    [[ "${MPI_HOME:-}" == *-avx2 ]]
    ;;
  8v100v0-avx512)
    export MD_CPU_ARCH=skylake-avx512 MD_CUDA_ARCH=70
    grep -qw avx512f /proc/cpuinfo
    ! grep -qw avx512_vnni /proc/cpuinfo
    [[ "${MPI_HOME:-}" == *-avx2 ]]
    ;;
  *) echo 'unvalidated MD target' >&2; exit 2 ;;
esac
set -u
