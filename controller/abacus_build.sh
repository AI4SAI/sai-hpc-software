#!/usr/bin/env bash
set -euo pipefail
target=$1
opts=(-DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$INSTALL_PREFIX"
      -DBUILD_TESTING=OFF -DGIT_SUBMODULE=OFF -DENABLE_LIBXC=ON
      -DENABLE_MPI=ON -DENABLE_OPENMP=ON -DENABLE_ELPA=ON
      -DENABLE_NATIVE_OPTIMIZATION=OFF)
case "$target" in
  cpu-misc) opts+=(-DUSE_CUDA=OFF);;
  v100) opts+=(-DUSE_CUDA=ON -DUSE_CUDA_MPI=ON -DCMAKE_CUDA_ARCHITECTURES=70);;
  a100) opts+=(-DUSE_CUDA=ON -DUSE_CUDA_MPI=ON -DCMAKE_CUDA_ARCHITECTURES=80);;
  *) exit 2;;
esac
cmake -S /workspace/source -B /workspace/build "${opts[@]}"
cmake --build /workspace/build --parallel "$BUILD_JOBS"
cmake --install /workspace/build
test -x "$INSTALL_PREFIX/bin/abacus"
"$INSTALL_PREFIX/bin/abacus" --info
