#!/usr/bin/env bash
set -euo pipefail
target=${1:?}
source /control/environment.sh "$target" cp2k
module unload gcc
module load gcc/14.3.0
export OMPI_CC=/opt/devtools/gcc/14.3.0/bin/gcc
export OMPI_CXX=/opt/devtools/gcc/14.3.0/bin/g++
export OMPI_FC=/opt/devtools/gcc/14.3.0/bin/gfortran
[[ "$($OMPI_FC -dumpfullversion)" == 14.3.0 ]]
ELPA_ROOT=$(python3 /control/cp2k_feature_contract.py site-elpa /workspace/candidate "$target" 67b5da876dd6a76b8b021d5a04d1c81ba79a4c50)
export ELPA_ROOT LD_LIBRARY_PATH="$ELPA_ROOT/lib:$LD_LIBRARY_PATH"
export PKG_CONFIG_PATH="$ELPA_ROOT/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
cd /workspace
"$MPI_HOME/bin/mpifort" -fopenmp $(pkg-config --variable=fcflags elpa_openmp) \
  /control/cp2k_elpa_abi.f90 $(pkg-config --libs elpa_openmp) -o probe
ldd ./probe | tee consumer-ldd.txt
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 timeout 60 \
  "$MPI_HOME/bin/mpiexec" --allow-run-as-root --bind-to none -n 1 ./probe | tee consumer.log
grep -q CP2K_SITE_ELPA_GCC14_PASS consumer.log
"$OMPI_FC" --version > compiler.txt
printf 'CP2K_SITE_ELPA_PREFLIGHT_PASS %s\n' "$target"

cmake -S /control -B /workspace/cmake-build \
  -DCMAKE_Fortran_COMPILER="$MPI_HOME/bin/mpifort" \
  -DCMAKE_LIBRARY_PATH=/opt/devtools/nvidia/cuda-12.9.1/lib64
cmake --build /workspace/cmake-build --parallel 2
cp /workspace/cmake-build/CMakeCache.txt /workspace/CMakeCache.txt
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 timeout 60 \
  "$MPI_HOME/bin/mpiexec" --allow-run-as-root --bind-to none -n 1 /workspace/cmake-build/consumer \
  | tee /workspace/cmake-consumer.log
python3 - "$ELPA_ROOT" "$target" <<'CHECK'
import re, sys
from pathlib import Path
cache=Path('/workspace/CMakeCache.txt').read_text()
values={m.group(1):m.group(2) for line in cache.splitlines() if (m:=re.fullmatch(r'([^:=]+):[^=]+=(.*)',line))}
assert values['pkgcfg_lib_CP2K_ELPA_elpa_openmp']==sys.argv[1]+'/lib/libelpa_openmp.so'
assert values['CP2K_ELPA_VERSION']=='2026.02.001'
if sys.argv[2]!='dsprhbm':
 for name in ['cudart','cublas']:
  assert values['pkgcfg_lib_CP2K_ELPA_'+name]=='/opt/devtools/nvidia/cuda-12.9.1/lib64/lib'+name+'.so'
print('CMAKE_SITE_ELPA_PASS '+sys.argv[2])
CHECK
