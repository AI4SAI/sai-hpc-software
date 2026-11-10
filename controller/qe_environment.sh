#!/usr/bin/env bash
# Use the site's CUDA-Fortran stack, including on the CPU-only target.
set -eo pipefail
set +u
export PATH=/usr/bin:/bin LD_LIBRARY_PATH= LD_PRELOAD=
export USER=${USER:-root} LOGNAME=${LOGNAME:-root}
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/devtools
module load openmpi/5.0.10-nvhpc26.3-pgi-cuda12-auto fftw/3.3.10
case "${1:?target required}" in
  dsprhbm) export QE_GPU= QE_DEPENDENCY_ISA=avx512; grep -qw avx512_vnni /proc/cpuinfo ;;
  4v100-avx512) export QE_GPU='openacc;cuda' QE_DEPENDENCY_ISA=avx512; grep -qw avx512_vnni /proc/cpuinfo ;;
  16v100-avx2) export QE_GPU='openacc;cuda' QE_DEPENDENCY_ISA=avx2; grep -qw avx2 /proc/cpuinfo; ! grep -qw avx512f /proc/cpuinfo ;;
  8v100v0-avx512) export QE_GPU='openacc;cuda' QE_DEPENDENCY_ISA=avx2; grep -qw avx512f /proc/cpuinfo; ! grep -qw avx512_vnni /proc/cpuinfo ;;
  *) echo 'unsupported QE target' >&2; return 2 ;;
esac
if [[ -n "$QE_GPU" ]]; then
  module load hdf5/1.14.6-pgi-ompi508
else
  # The build selects and verifies the pinned NVHPC/F90 application provider.
  # CPU runtime uses static HDF5 and needs no HDF5 module or external prefix.
  export HDF5_ROOT=
fi
[[ "$MPI_HOME" == "/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-pgi-cuda12-$QE_DEPENDENCY_ISA" ]]
[[ "$(mpif90 --showme:command)" == nvfortran ]]
export QE_COMPILER_ROOT=/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/compilers
test -r "$MPI_HOME/lib/libscalapack_lp64.so"
test -r "$QE_COMPILER_ROOT/lib/liblapack_lp64.so"
export CC=nvc CXX=nvc++ FC=nvfortran
# Keep static HDF5's zlib/szip dependencies resolvable in the minimal rootfs.
export LD_LIBRARY_PATH="$MPI_HOME/lib:$QE_COMPILER_ROOT/lib:$FFTW_ROOT/lib:${HDF5_ROOT:+$HDF5_ROOT/lib:}${LD_LIBRARY_PATH:-}"
set -u
