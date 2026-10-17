#!/bin/bash
set -euo pipefail
source /control/environment.sh 4v100-avx512
set +u
module unload gcc
module load gcc/14.3.0
set -u
spla=/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-r9c87f65a7c0a/4V100/dependencies/spack/linux-zen5/spla-1.6.1-uc273bzgwxxngrexd366xlfvkfmeuvgm
cuda=/opt/devtools/nvidia/cuda-12.9.1
export LD_LIBRARY_PATH="$spla/lib:$spla/lib64:$LD_LIBRARY_PATH"
export TMPDIR=/probe/runtime CUDA_CACHE_PATH=/probe/cuda-cache
mkdir -p "$TMPDIR" "$CUDA_CACHE_PATH"
libdir="$spla/lib"
if [[ ! -f "$libdir/libspla.so" ]]; then libdir="$spla/lib64"; fi
g++ --version
ldd "$libdir/libspla.so"
sha256sum "$libdir/libspla.so" /probe/input/spla-probe.cpp
# SpLA 1.6.1 calls cuBLAS; it compiles no custom .cu kernels and has no
# Spack cuda_arch variant. CUDA selects the V100 kernels at runtime.
g++ -O3 -std=c++17 -I"$spla/include" -I"$cuda/include" /probe/input/spla-probe.cpp \
  -L"$spla/lib" -L"$spla/lib64" -L"$cuda/lib64" -lspla -lcudart -lcublas -ldl \
  -Wl,--export-dynamic -o /probe/spla-probe
for threads in 1 4; do
  OMP_NUM_THREADS=$threads OPENBLAS_NUM_THREADS=$threads OMP_DYNAMIC=FALSE \
    /probe/spla-probe > "/probe/results/spla-threads-$threads.json"
done
