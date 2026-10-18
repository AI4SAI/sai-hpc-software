#!/usr/bin/env bash
# Source inside a container or on a compute node. Match the site's 26.3 stack.
elpa_isa=${1:?avx2 or avx512 required}
case "$elpa_isa" in avx2|avx512) ;; *) return 2 ;; esac
set +u
source /etc/profile.d/lmod.sh
module use /opt/modules/modulefiles/devtools
module load openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto saiblas/2603-gnu-auto
set -u
export ELPA_ROOT=/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-${elpa_isa}/nvidia
[[ "$MPI_HOME" == /opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-${elpa_isa} ]]
[[ "$OPENBLAS_ROOT" == /opt/devtools/saiblas/2603-gnu-${elpa_isa} ]]
export OMPI_CC=/usr/bin/gcc OMPI_CXX=/usr/bin/g++ OMPI_FC=/usr/bin/gfortran
export LD_LIBRARY_PATH="$ELPA_ROOT/lib:${LD_LIBRARY_PATH:-}"
export PKG_CONFIG_PATH="$ELPA_ROOT/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
export PATH="$ELPA_ROOT/bin:$PATH"
export OMP_NUM_THREADS=${OMP_NUM_THREADS:-2} OPENBLAS_NUM_THREADS=1
export CUDA_CACHE_PATH=${TMPDIR:?}/cuda-cache
export PYTHONDONTWRITEBYTECODE=1
