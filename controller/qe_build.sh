#!/usr/bin/env bash
set -euo pipefail
: "${INSTALL_PREFIX:?}" "${BUILD_JOBS:?}" "${QE_COMPILER_ROOT:?}"
mkdir -p "$INSTALL_PREFIX/bin" "$INSTALL_PREFIX/share/sai" "$INSTALL_PREFIX/share/licenses/qe"
# Match the site's pwall suite. QE libraries are statically incorporated into
# these programs; installing only the built tools avoids shipping source/objects
# and does not require building unrelated EPW/GWW targets.
options=(
  -DCMAKE_INSTALL_PREFIX="$INSTALL_PREFIX" -DCMAKE_BUILD_TYPE=Release
  -DCMAKE_PROJECT_qe_INCLUDE=/control/qe_extra_tools.cmake
  -DCMAKE_C_COMPILER=nvc -DCMAKE_Fortran_COMPILER=nvfortran -DCMAKE_CXX_COMPILER=nvc++
  '-DCMAKE_Fortran_FLAGS_RELEASE=-O3 -tp=host' '-DCMAKE_C_FLAGS_RELEASE=-O3 -tp=host'
  '-DCMAKE_CXX_FLAGS_RELEASE=-O3 -tp=host'
  -DBUILD_SHARED_LIBS=OFF -DQE_ENABLE_MPI=ON -DQE_ENABLE_OPENMP=ON
  -DQE_ENABLE_HDF5=ON -DHDF5_USE_STATIC_LIBRARIES=ON -DHDF5_ROOT="$HDF5_ROOT"
  -DQE_ENABLE_SCALAPACK=ON -DQE_ENABLE_OSCDFT=ON -DQE_ENABLE_TEST=OFF
  -DQE_ENABLE_DOC=OFF -DQE_GPU="$QE_GPU" -DBLA_VENDOR=NVHPC
  -DSCALAPACK_scalapack_LIBRARY="$MPI_HOME/lib/libscalapack_lp64.so"
  -DQE_FFTW_VENDOR=FFTW3 -DFFTW3_ROOT="$FFTW_ROOT"
  -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=ON
  '-DCMAKE_INSTALL_RPATH=$ORIGIN/../lib'
)
if [[ -n "$QE_GPU" ]]; then
  options+=(-DQE_GPU_ARCHS=sm_70 -DQE_ENABLE_MPI_GPU_AWARE=ON)
else
  options+=(-DQE_ENABLE_MPI_GPU_AWARE=OFF)
fi
printf '%q ' cmake -S /workspace/source -B /workspace/build "${options[@]}" > "$INSTALL_PREFIX/share/sai/configure-command.txt"
cmake -S /workspace/source -B /workspace/build "${options[@]}"
cmake --build /workspace/build --parallel "$BUILD_JOBS" --target pwall upf \
  qe_pp_wannier2pw_exe qe_upflib_casino2upf_exe sai_qe_tools
for binary in /workspace/build/bin/*.x; do
  test -x "$binary"
  install -m 0555 "$binary" "$INSTALL_PREFIX/bin/"
done
# QE dispatches this standard PW entry point by argv[0]. Keep the relative link
# instead of resolving it to pw.x or duplicating the executable bytes. dist.x
# is not published: its early-return path frees uninitialized MPI communicators.
ln -s pw.x "$INSTALL_PREFIX/bin/manypw.x"
install -m 0444 /workspace/source/License "$INSTALL_PREFIX/share/licenses/qe/License"
for library in devxlib fox mbd wannier90; do
  mkdir -p "$INSTALL_PREFIX/share/licenses/$library"
  for file in /workspace/source/external/"$library"/{LICENSE*,COPYING*,COPYRIGHT*,license*}; do
    [[ ! -f "$file" ]] || install -m 0444 "$file" "$INSTALL_PREFIX/share/licenses/$library/"
  done
done
cp /workspace/build/CMakeCache.txt "$INSTALL_PREFIX/share/sai/CMakeCache.txt"
