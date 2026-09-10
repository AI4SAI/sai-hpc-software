#!/usr/bin/env bash
set -euo pipefail
target=$1
opts=(-DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$INSTALL_PREFIX"
      -DBUILD_TESTING=OFF -DGIT_SUBMODULE=OFF -DENABLE_LIBXC=ON
      -DENABLE_MPI=ON -DENABLE_OPENMP=ON -DENABLE_ELPA=ON
      -DENABLE_NATIVE_OPTIMIZATION=OFF)
case "$target" in
  dsprhbm)
    cpu_arch=x86-64-v4
    opts+=(-DUSE_CUDA=OFF)
    ;;
  4v100-avx512)
    cpu_arch=znver4
    opts+=(-DUSE_CUDA=ON -DUSE_CUDA_MPI=ON -DCMAKE_CUDA_ARCHITECTURES=70)
    ;;
  16v100-avx2)
    cpu_arch=znver3
    opts+=(-DUSE_CUDA=ON -DUSE_CUDA_MPI=ON -DCMAKE_CUDA_ARCHITECTURES=70)
    ;;
  a100)
    cpu_arch=x86-64-v3
    opts+=(-DUSE_CUDA=ON -DUSE_CUDA_MPI=ON -DCMAKE_CUDA_ARCHITECTURES=80)
    ;;
  *) exit 2;;
esac
if [[ "$target" != dsprhbm ]]; then
  opts+=(-DENABLE_CUSOLVERMP=ON -DENABLE_CUBLASMP=ON
         -DENABLE_NCCL_PARALLEL_DEVICE=ON)
fi
opts+=("-DCMAKE_C_FLAGS=-march=$cpu_arch -mtune=$cpu_arch"
      "-DCMAKE_CXX_FLAGS=-march=$cpu_arch -mtune=$cpu_arch")
# ABACUS commit 1497232 omits the declarations used by its cuSOLVERMp CUDA
# translation unit; newer upstream sources include these headers. Keep the
# source SHA unchanged while applying the minimal compatibility fix in-recipe.
if [[ "$target" != dsprhbm ]]; then
  sed -i '/#include "source_base\/module_device\/device_check.h"/a #include "source_base/global_variable.h"\n#include "source_base/global_function.h"' \
    /workspace/source/source/source_hsolver/kernels/cuda/diag_cusolvermp.cu
fi
cmake -S /workspace/source -B /workspace/build "${opts[@]}"
cmake --build /workspace/build --parallel "$BUILD_JOBS"
cmake --install /workspace/build
test -x "$INSTALL_PREFIX/bin/abacus"
"$INSTALL_PREFIX/bin/abacus" --info

# Keep a small scientific GPU case inside the single SIF for end-to-end MPI
# acceptance. The runtime controller copies only these input data to its run
# directory; it never expands the upstream source tree on the host.
if [[ "$target" == *v100* || "$target" == a100 ]]; then
  fixture="$INSTALL_PREFIX/share/sai/smoke-case"
  mkdir -p "$fixture/PP_ORB"
  cp -L /workspace/source/tests/11_PW_GPU/scf_cg/{INPUT,KPT,STRU} "$fixture/"
  sed -i 's#../../PP_ORB#./PP_ORB#g' "$fixture/INPUT"
  printf '\nkpar 2\nbndpar 1\n' >> "$fixture/INPUT"
  cp -L /workspace/source/tests/PP_ORB/{As_dojo.upf,Ga_dojo.upf} "$fixture/PP_ORB/"
fi
