#!/usr/bin/env bash
# Evaluated inside the container. Dependency absolute paths match the host.
set -eo pipefail
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
module load cuda/12.9.1
export CMAKE_LIBRARY_PATH=${LIBRARY_PATH:-} CMAKE_INCLUDE_PATH=${CPATH:-}
set -u
