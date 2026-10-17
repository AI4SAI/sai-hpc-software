#!/bin/bash
set -euo pipefail
source /control/environment.sh 4v100-avx512
set +u
module unload gcc
module load gcc/14.3.0
set -u
export CC=/opt/devtools/gcc/14.3.0/bin/gcc CXX=/opt/devtools/gcc/14.3.0/bin/g++
export FC=/opt/devtools/gcc/14.3.0/bin/gfortran
export OMPI_CC="$CC" OMPI_CXX="$CXX" OMPI_FC="$FC" PYTHONPATH=/control
prefix=/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-r9c87f65a7c0a/4V100
python3 /probe/input/cp2k_spack_link.py /dep-results/install-success.json 4V100 "$prefix" > /probe/build-env.sh
source /probe/build-env.sh
cmake_prefix=$(IFS=';'; echo "${cp2k_spack_prefixes[*]}")
cmake -S /probe/input/config-source -B /probe/config-build \
  -DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX" -DCMAKE_Fortran_COMPILER="$FC" \
  -DCMAKE_PREFIX_PATH="$cmake_prefix" -DCMAKE_BUILD_TYPE=Release
