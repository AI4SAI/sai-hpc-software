#!/usr/bin/env bash
set -eo pipefail
run=${1:?run}; original=${2:?original CPU build}
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/devtools
module load cmake/3.31.6 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto fftw/3.3.10 libxc/7.0.0-auto saiblas/2603-gnu-auto
module unload gcc
module load gcc/14.3.0 spglib/2.7.0
set -u
export OMPI_CC=/opt/devtools/gcc/14.3.0/bin/gcc
export PRTE_MCA_prte_tmpdir_base=$TMPDIR OMPI_MCA_orte_tmpdir_base=$TMPDIR
# The identical receipt supplies the original sealed Spack library directories.
source "$original/work/spack-env.sh"
export LD_LIBRARY_PATH="$cp2k_spack_runtime_ld:${LD_LIBRARY_PATH:-}"
python3 "$run/control/deployment/cp2k-water-scaling/relink-cpu.py" "$run" "$original"
