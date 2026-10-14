#!/usr/bin/env bash
# Sourced after site modules. A broad ML prefix must not select a second
# NCCL/CUDA installation ahead of FindLibrary/FindProgram's module hints.
: "${NCCL_ROOT:?site NCCL module required}" "${CUDA_HOME:?site CUDA module required}"
[[ "$NCCL_ROOT" == /* && "$CUDA_HOME" == /* ]]
test -s "$NCCL_ROOT/include/nccl.h"
test -s "$NCCL_ROOT/lib/libnccl.so"
test -x "$CUDA_HOME/bin/nvcc"
test -s "$CUDA_HOME/include/cuda.h"
test -s "$CUDA_HOME/lib64/libcudart.so"
# Check the actual DSO, not only a header version. cuBLASMp requires these
# newer symbols; the DeepMD Conda NCCL 2.28.9 does not export them.
abacus_nccl_symbols=$(nm -D --defined-only "$NCCL_ROOT/lib/libnccl.so")
for symbol in ncclPutSignal ncclWaitSignal ncclCommQueryProperties; do
  if ! grep -Eq "[[:space:]]${symbol}(@@?[^[:space:]]+)?$" <<< "$abacus_nccl_symbols"; then
    echo "Site NCCL lacks cuBLASMp symbol: $symbol" >&2
    return 1
  fi
done
unset abacus_nccl_symbols
export CMAKE_PREFIX_PATH="$NCCL_ROOT:$CUDA_HOME${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
export LD_LIBRARY_PATH="$NCCL_ROOT/lib:$CUDA_HOME/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
abacus_gpu_options=(
  "-DNCCL_LIBRARY:FILEPATH=$NCCL_ROOT/lib/libnccl.so"
  "-DNCCL_INCLUDE_DIR:PATH=$NCCL_ROOT/include"
  "-DCMAKE_CUDA_COMPILER:FILEPATH=$CUDA_HOME/bin/nvcc"
  "-DCUDAToolkit_ROOT:PATH=$CUDA_HOME")

abacus_check_site_gpu() {
  local cache=$1 option row expected alternate
  for option in "${abacus_gpu_options[@]}"; do
    expected=${option#-D}
    # CMake's _cmake_find_compiler_path(CUDA) converts an existing compiler
    # cache entry to STRING even when the command line explicitly used FILEPATH.
    # Permit only that known type conversion, never another compiler value.
    alternate=$expected
    if [[ "$expected" == CMAKE_CUDA_COMPILER:FILEPATH=* ]]; then
      alternate=${expected/CMAKE_CUDA_COMPILER:FILEPATH=/CMAKE_CUDA_COMPILER:STRING=}
    fi
    if ! grep -Fxq -e "$expected" -e "$alternate" "$cache"; then
      echo "GPU discovery escaped the site modules: ${option#-D}" >&2
      return 1
    fi
  done
  # FindCUDAToolkit has many discovered libraries; reject residual Conda CUDA
  # selections too, even for components not directly linked by ABACUS today.
  while IFS= read -r row; do
    if [[ "$row" == *=/opt/apps/conda_env/* ]]; then
      echo "Conda CUDA/NCCL cache contamination: $row" >&2
      return 1
    fi
  done < <(grep -E '^(CUDA_|CUDAToolkit_|CMAKE_CUDA_|NCCL_)[^=]*=' "$cache")
  echo "SITE_GPU_CONFIG_VERIFIED $NCCL_ROOT $CUDA_HOME"
}
