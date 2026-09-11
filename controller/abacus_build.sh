#!/usr/bin/env bash
set -euo pipefail
target=$1
opts=(-DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$INSTALL_PREFIX"
      -DBUILD_TESTING=OFF -DGIT_SUBMODULE=OFF -DENABLE_LIBXC=ON
      -DENABLE_MPI=ON -DENABLE_OPENMP=ON -DENABLE_ELPA=ON
      -DENABLE_NATIVE_OPTIMIZATION=OFF)
# ISA-level values (x86-64-v3/v4) are only valid for -march; -mtune needs a
# micro-architecture name, so it is set separately per target.
cpu_tune=generic
case "$target" in
  dsprhbm)
    cpu_arch=x86-64-v4
    cpu_tune=sapphirerapids
    opts+=(-DUSE_CUDA=OFF)
    ;;
  4v100-avx512)
    cpu_arch=znver4
    cpu_tune=znver4
    opts+=(-DUSE_CUDA=ON -DUSE_CUDA_MPI=ON -DCMAKE_CUDA_ARCHITECTURES=70)
    ;;
  16v100-avx2)
    cpu_arch=znver3
    cpu_tune=znver3
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
opts+=("-DCMAKE_C_FLAGS=-march=$cpu_arch -mtune=$cpu_tune"
      "-DCMAKE_CXX_FLAGS=-march=$cpu_arch -mtune=$cpu_tune")
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

# Keep a small scientific case inside the single SIF for end-to-end MPI
# acceptance. The runtime controller copies only these input data to its run
# directory; it never expands the upstream source tree on the host. GPU
# targets run it as-is; CPU targets sed the device line to cpu before use.
fixture="$INSTALL_PREFIX/share/sai/smoke-case"
mkdir -p "$fixture/PP_ORB"
cp -L /workspace/source/tests/11_PW_GPU/scf_cg/{INPUT,KPT,STRU} "$fixture/"
sed -i 's#../../PP_ORB#./PP_ORB#g' "$fixture/INPUT"
printf '\nkpar 2\nbndpar 1\n' >> "$fixture/INPUT"
cp -L /workspace/source/tests/PP_ORB/{As_dojo.upf,Ga_dojo.upf} "$fixture/PP_ORB/"

# Exercise distinct distributed GPU code paths, not only the PW smoke case.
# All sources and PP/orbital files are copied inside the build overlay; only
# these small input fixtures are installed into the final SIF.
if [[ "$target" != dsprhbm ]]; then
  fixtures="$INSTALL_PREFIX/share/sai/gpu-cases"
  mkdir -p "$fixtures/cusolvermp/PP_ORB" "$fixtures/nccl/PP_ORB"
  cp -L /workspace/source/tests/12_NAO_Gamma_GPU/009_NO_Si2_DZP_GPU/{INPUT,KPT,STRU} \
    "$fixtures/cusolvermp/"
  cp -L /workspace/source/tests/PP_ORB/{Si_ONCV_PBE-1.0.upf,Si_gga_8au_100Ry_2s2p1d.orb} \
    "$fixtures/cusolvermp/PP_ORB/"
  cp -L /workspace/source/tests/11_PW_GPU/scf_bpcg/{INPUT,KPT,STRU} "$fixtures/nccl/"
  cp -L /workspace/source/tests/PP_ORB/{As_dojo.upf,Ga_dojo.upf} "$fixtures/nccl/PP_ORB/"
  for gpu_case in cusolvermp nccl; do
    sed -i 's#../../PP_ORB#./PP_ORB#g' "$fixtures/$gpu_case/INPUT" "$fixtures/$gpu_case/STRU"
    sed -i -E '/^[[:space:]]*(ks_solver|device|kpar|bndpar)[[:space:]]/d' \
      "$fixtures/$gpu_case/INPUT"
  done
  printf '\nks_solver cusolvermp\ndevice gpu\nkpar 1\nbndpar 1\n' >> "$fixtures/cusolvermp/INPUT"
  printf '\nks_solver bpcg\ndevice gpu\nkpar 1\nbndpar 2\n' >> "$fixtures/nccl/INPUT"
fi
