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
links="-L$DEEPMD_ROOT/lib -L$GPUMD_PLUMED_PREFIX/lib -Xlinker=-rpath -Xlinker=$DEEPMD_ROOT/lib -Xlinker=-rpath -Xlinker=$GPUMD_PLUMED_PREFIX/lib"
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
tar --exclude='*.o' --exclude='*.obj' --exclude=gpumd --exclude=nep --exclude=gnep \
  -cf - . | tar -xf - -C "$INSTALL_PREFIX/share/gpumd/src"
cp /workspace/source/LICENCE "$INSTALL_PREFIX/share/gpumd/"
python3 /control/gpumd_science.py prepare /workspace/source "$INSTALL_PREFIX/share/sai/cases"
