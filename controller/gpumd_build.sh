#!/usr/bin/env bash
set -euo pipefail
: "${INSTALL_PREFIX:?}" "${BUILD_JOBS:?}" "${GPUMD_CUDA_ARCH:?}"
cd /workspace/source/src
test -s makefile
# Never silently lose a feature if upstream changes its build interface.
grep -Rq 'USE_DEEPMD' force
grep -Rq 'USE_PLUMED' measure
flags="-std=c++14 -O3 -arch=sm_$GPUMD_CUDA_ARCH -DUSE_DEEPMD -DUSE_PLUMED -Xcompiler=-march=$GPUMD_CPU_ARCH,-mtune=$GPUMD_CPU_ARCH"
includes="-I./ -I$DEEPMD_ROOT/include/deepmd -I$GPUMD_PLUMED_PREFIX/include"
links="-L$DEEPMD_ROOT/lib -L$GPUMD_PLUMED_PREFIX/lib -Xlinker=-rpath -Xlinker=$DEEPMD_ROOT/lib -Xlinker=-rpath -Xlinker=$GPUMD_PLUMED_PREFIX/lib -Xlinker=-rpath-link -Xlinker=$GPUMD_SYSTEM_BLAS_PATH"
libraries='-lcublas -lcusolver -lcufft -ldeepmd_cc -ldeepmd_c -lplumed -lplumedKernel'
executables=(gpumd nep)
if grep -q '^gnep:' makefile; then executables+=(gnep); fi
mkdir -p "$INSTALL_PREFIX/bin" "$INSTALL_PREFIX/share/sai" "$INSTALL_PREFIX/share/gpumd/src"
printf '%s\n' "CFLAGS=$flags" "INC=$includes" "LDFLAGS=$links" "LIBS=$libraries" \
  > "$INSTALL_PREFIX/share/sai/build-options.txt"
make -j "$BUILD_JOBS" "CC=nvcc" "CFLAGS=$flags" "INC=$includes" \
  "LDFLAGS=$links" "LIBS=$libraries" "${executables[@]}"
for name in "${executables[@]}"; do
  install -m 0555 "$name" "$INSTALL_PREFIX/bin/$name"
done
printf '%s\n' "${executables[@]}" > "$INSTALL_PREFIX/share/sai/executables.txt"
# NEP JIT requires source at runtime. Keep the full small source/header tree,
# not build objects or a hardcoded reference to /workspace/source.
# Build options are Make command-line overrides, not source edits. Refuse to
# discard any tracked change, then archive the already verified commit's src
# tree instead of traversing the mutable overlay directory (tar's '.' changed
# during the real build). Both archive and extraction failures remain fatal.
git -C /workspace/source diff --quiet HEAD -- src
source_tree=$(git -C /workspace/source rev-parse HEAD:src)
git -C /workspace/source archive --format=tar "$source_tree" | \
  tar --exclude='*.o' --exclude='*.obj' --exclude=gpumd --exclude=nep --exclude=gnep \
      -xf - -C "$INSTALL_PREFIX/share/gpumd/src"
printf '%s\n' "$source_tree" > "$INSTALL_PREFIX/share/sai/jit-source-tree"
cp /workspace/source/LICENCE "$INSTALL_PREFIX/share/gpumd/"
python3 /control/gpumd_science.py prepare /workspace/source "$INSTALL_PREFIX/share/sai/cases"
