#!/usr/bin/env bash
set -euo pipefail
target=${1:?target required}
jobs=${BUILD_JOBS:-8}
source_sha=${CP2K_SOURCE_SHA:?source SHA required}
site=/opt/apps/cp2k/cp2k-2026.1/tools/toolchain/install
prefix=${INSTALL_PREFIX:?install prefix required}

case "$target" in
  dsprhbm) accel=NONE; gpu_arch=; cpu_flags=-march=x86-64-v4; dbcsr="$site/dbcsr-2.9.0/lib/cmake/dbcsr" ;;
  4v100-avx512) accel=CUDA; gpu_arch=70; cpu_flags=-march=znver4; dbcsr="$site/dbcsr-2.9.0-cuda/lib/cmake/dbcsr" ;;
  16v100-avx2) accel=CUDA; gpu_arch=70; cpu_flags=-march=znver3; dbcsr="$site/dbcsr-2.9.0-cuda/lib/cmake/dbcsr" ;;
  8v100v0-avx512)
    accel=CUDA; gpu_arch=70; cpu_flags='-march=native -mtune=native'
    dbcsr="$site/dbcsr-2.9.0-cuda/lib/cmake/dbcsr"
    native=$(gcc -march=native -mtune=native -Q --help=target)
    printf '%s\n' "$native" | grep -E 'march=|mtune=|mavx'
    grep -Eq 'march=[[:space:]]+skylake-avx512' <<< "$native"
    grep -q 'Gold 6146' /proc/cpuinfo
    ;;
  a100) accel=CUDA; gpu_arch=80; cpu_flags=-march=x86-64-v3; dbcsr="$site/dbcsr-2.9.0-cuda/lib/cmake/dbcsr" ;;
  *) echo "unknown CP2K target: $target" >&2; exit 2 ;;
esac

module purge
module use /opt/modules/modulefiles/devtools
module load cmake/3.31.6 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto \
  fftw/3.3.10 libxc/7.0.0-auto saiblas/2603-gnu-auto elpa/2026.02.001-2603-gnu
if [[ "$accel" == CUDA ]]; then module load cuda/12.9.1 nvmplibs/26.7-tmp; fi
if [[ "$target" == 8v100v0-avx512 ]]; then
  [[ "${OPAL_PREFIX:?}" == *-avx2 ]]
  [[ "${OPENBLAS_ROOT:?}" == *-avx2 ]]
fi

new_deps=/input/dependencies/tblite-dependencies.tar.gz
[[ -s "$new_deps" ]] || { echo "verified tblite/DFT-D4 dependency bundle is required" >&2; exit 1; }
tblite_root=/opt/software/cp2k-dependencies/tblite
mkdir -p "$tblite_root"
tar --no-same-owner --strip-components=1 -xzf "$new_deps" -C "$tblite_root"

prefixes=(
  "$dbcsr" "$site/COSMA-2.7.0-cuda" "$site/COSMA-2.7.0"
  "$site/SpLA-1.6.1-cuda" "$site/SpLA-1.6.1" "$site/elpa-2024.05.001/nvidia"
  "$site/elpa-2024.05.001/cpu" "$site/scalapack-2.2.2" "$site/libxc-7.0.0"
  "$site/libint-v2.6.0-cp2k-lmax-5" "$site/fftw-3.3.10" "$site/openblas-0.3.30"
  "$site/libxsmm-e0c4a2389afba36c453233ad7de07bd92c715bec" "$site/spglib-2.5.0"
  "$site/libvori-220621" "$site/hdf5-1.14.6" "$site/plumed-2.9.3" "$tblite_root"
)
cmake_prefix=$(IFS=';'; echo "${prefixes[*]}")
mkdir -p /workspace/build "$prefix/bin" "$prefix/share/sai"
cmake_args=(
  -S /workspace/source -B /workspace/build -DCMAKE_BUILD_TYPE=Release
  -DCMAKE_INSTALL_PREFIX="$prefix" -DCMAKE_INSTALL_LIBDIR=lib64
  -DCMAKE_PREFIX_PATH="$cmake_prefix" -DDBCSR_DIR="$dbcsr"
  -DCP2K_USE_MPI=ON -DCP2K_USE_MPI_F08=ON -DCP2K_USE_ACCEL="$accel"
  -DCP2K_USE_DBCSR_CONFIG=ON -DCP2K_USE_FFTW3=ON -DCP2K_USE_LIBXC=ON
  -DCP2K_USE_LIBINT2=ON -DCP2K_USE_ELPA=ON -DCP2K_USE_COSMA=ON
  -DCP2K_USE_LIBXS=ON -DCP2K_USE_LIBXSMM=ON -DCP2K_USE_PLUMED=ON
  -DCP2K_USE_SPGLIB=ON -DCP2K_USE_VORI=ON -DCP2K_USE_HDF5=ON
  -DCP2K_USE_DFTD4=ON -DCP2K_USE_TBLITE=ON
  -DCMAKE_Fortran_FLAGS="-O3 $cpu_flags" -DCMAKE_C_FLAGS="-O3 $cpu_flags"
  -DCMAKE_CXX_FLAGS="-O3 $cpu_flags"
)
if [[ "$accel" == CUDA ]]; then
  cmake_args+=(
    -DCP2K_WITH_GPU=V100 -DCP2K_USE_CUDA=ON
    -DCMAKE_CUDA_ARCHITECTURES="$gpu_arch" -DCP2K_USE_CUSOLVER_MP=ON
    -DCP2K_CUSOLVER_MP_ROOT=/opt/devtools/nvidia/mp_libs
    -DCP2K_NCCL_ROOT=/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/comm_libs/12.9/nccl-2.29
  )
else
  cmake_args+=( -DCP2K_USE_CUSOLVER_MP=OFF )
fi
cmake "${cmake_args[@]}"
cmake --build /workspace/build --parallel "$jobs"
cmake --install /workspace/build

printf '%s\n' "$source_sha" > "$prefix/share/sai/source-sha"
printf '%s\n' "$target" > "$prefix/share/sai/target"
module -t list > "$prefix/share/sai/modules.txt" 2>&1 || true
cp /workspace/build/CMakeCache.txt "$prefix/share/sai/CMakeCache.txt"
runtime_ld="$prefix/lib64:$prefix/lib:${LD_LIBRARY_PATH:-}"
printf 'export PATH=%q\nexport LD_LIBRARY_PATH=%q\n' "$prefix/bin:$PATH" "$runtime_ld" > "$prefix/share/sai/runtime-env.sh"
chmod 0555 "$prefix/bin/cp2k.psmp"
export LD_LIBRARY_PATH="$runtime_ld"
ldd "$prefix/bin/cp2k.psmp" | tee "$prefix/share/sai/ldd.txt"
! grep -q 'not found' "$prefix/share/sai/ldd.txt"
