#!/usr/bin/env bash
set -euo pipefail
target=${1:?target required}
jobs=${BUILD_JOBS:-8}
source_sha=${CP2K_SOURCE_SHA:?source SHA required}
prefix=${INSTALL_PREFIX:?install prefix required}

case "$target" in
  dsprhbm) accel=NONE; gpu_arch=; expected_partition=DSPRHBM ;;
  4v100-avx512) accel=CUDA; gpu_arch=70; expected_partition=4V100 ;;
  16v100-avx2) accel=CUDA; gpu_arch=70; expected_partition=16V100 ;;
  8v100v0-avx512) accel=CUDA; gpu_arch=70; expected_partition=8V100V0 ;;
  *) echo "unknown CP2K target: $target" >&2; exit 2 ;;
esac
[[ "${SAI_BUILD_PARTITION:?native build partition must be recorded}" == "$expected_partition" ]]
cpu_flags='-march=native -mtune=native'

# environment.sh already resolved ISA-aware dependencies on this compute node.
# Do not purge it: that would also discard DSPRHBM's explicit GCC module.
if [[ "$accel" == CUDA ]]; then module load cuda/12.9.1 nvmplibs/26.7-tmp; fi
module load gcc/13.3.0 spglib/2.7.0
[[ "${SPGLIB_ROOT:?}" == /opt/devtools/spglib/spglib-2.7.0 ]]
python3 /control/cp2k_spack_link.py /results/install-success.json "$expected_partition" "$prefix" \
  > /workspace/cp2k-spack-build-env.sh
source /workspace/cp2k-spack-build-env.sh
dbcsr="$prefix/dependencies/dbcsr/lib64/cmake/dbcsr"
[[ "${ELPA_ROOT:?}" == /opt/devtools/elpa/elpa-2026.02.001-2603-gnu/* ]]
# The standalone ELPA module selects its NVIDIA build on CPU nodes too.
# Its .pc file lists CUDA libraries without a CUDA -L, and the CUDA module
# sets LD_LIBRARY_PATH but not LIBRARY_PATH. Give FindPkgConfig an explicit
# search directory so it resolves full paths instead of retaining -lcudart.
# This does not enable CP2K GPU offload on DSPRHBM.
cuda_libraries=/opt/devtools/nvidia/cuda-12.9.1/lib64
for library in cusolver cudart cublasLt cublas; do
  [[ -f "$cuda_libraries/lib$library.so" ]] || {
    echo "standalone ELPA requires $cuda_libraries/lib$library.so" >&2; exit 1;
  }
done
if [[ "$target" == 8v100v0-avx512 ]]; then
  # Gold 6146 supports native AVX-512 but lacks VNNI; site dependencies use AVX2.
  [[ "${OPAL_PREFIX:?}" == *-avx2 ]]
  [[ "${OPENBLAS_ROOT:?}" == *-avx2 ]]
fi

tblite_root="$prefix/dependencies/tblite"
# The successful 4V100 bundle establishes compatible source versions, not
# permission to reuse its own compiled libraries across ISA targets. Rebuild
# its seven locked sources natively inside each target's overlay.
export CP2K_NATIVE_FLAGS="-O3 $cpu_flags"
bash /control/cp2k_dependencies.sh /input/probe /workspace/tblite-build "$tblite_root"

hdf5_root="$CP2K_HDF5_ROOT"
grep -Eq 'Parallel HDF5:[[:space:]]+yes' "$hdf5_root"/lib*/libhdf5.settings

prefixes=(
  "$ELPA_ROOT" "$OPENBLAS_ROOT" "$LIBXC_ROOT" "$FFTW_ROOT" "$tblite_root"
  "$SPGLIB_ROOT" "${cp2k_spack_prefixes[@]}"
)
for dependency in "${prefixes[@]}"; do
  [[ -d "$dependency" ]] || { echo "required dependency missing: $dependency" >&2; exit 1; }
  export PKG_CONFIG_PATH="${PKG_CONFIG_PATH:+$PKG_CONFIG_PATH:}$dependency/lib/pkgconfig:$dependency/lib64/pkgconfig"
done
# Explicit adapters resolve only the exact private roots from the verified
# Spack installation receipt, including lib/lib64 layout differences.
mkdir -p "$prefix/share/sai/cmake"
cp /control/cp2k_Libint2Config.cmake "$prefix/share/sai/cmake/Libint2Config.cmake"
cp /control/cp2k_libxsmmConfig.cmake "$prefix/share/sai/cmake/libxsmmConfig.cmake"

# CP2K 2026.2 needs LIBXS in addition to LIBXSMM. Build the upstream-pinned
# dependency inside this target's overlay, with the same native compiler flags.
libxs_archive=/input/dependencies/libxs-1.0.0.tar.gz
printf '%s  %s\n' de26f50cb986a2f0e4f92c0eb489d40a44f7e4c5acd22751a6cfa2829dabd04d "$libxs_archive" | sha256sum --check --status
mkdir -p /workspace/libxs-source
tar --no-same-owner --strip-components=1 -xzf "$libxs_archive" -C /workspace/libxs-source
patch -d /workspace/libxs-source -p1 < /workspace/source/tools/toolchain/scripts/stage4/libxs-1.0.0-jit-handle.patch
cmake -S /workspace/libxs-source -B /workspace/libxs-build \
  -DCMAKE_INSTALL_PREFIX="$prefix/dependencies/libxs" -DCMAKE_INSTALL_LIBDIR=lib \
  -DCMAKE_BUILD_TYPE=Release -DLIBXS_FORTRAN=ON -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
  -DCMAKE_C_FLAGS="-O3 $cpu_flags" -DCMAKE_Fortran_FLAGS="-O3 $cpu_flags"
cmake --build /workspace/libxs-build --parallel "$jobs"
cmake --install /workspace/libxs-build
prefixes+=("$prefix/dependencies/libxs")
# Current development requires wignernj; older releases do not. The source
# archive is fetched and hashed on the runner, never on an offline compute node.
if grep -Eq 'find_package\(wignernj[[:space:]]' /workspace/source/CMakeLists.txt; then
  wigner_archive=/input/updates/libwignernj-0.8.0.tar.gz
  printf '%s  %s\n' 7220cea92652040d6456aba92ff151124d9c69ce8695840490c18dd25a0da80c "$wigner_archive" | sha256sum --check --status
  mkdir -p /workspace/wignernj-source
  tar --no-same-owner --strip-components=1 -xzf "$wigner_archive" -C /workspace/wignernj-source
  cmake -S /workspace/wignernj-source -B /workspace/wignernj-build \
    -DCMAKE_INSTALL_PREFIX="$prefix/dependencies/wignernj" -DCMAKE_INSTALL_LIBDIR=lib \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DBUILD_SHARED_LIBS=OFF \
    -DWIGNERNJ_BUILD_FORTRAN=OFF -DWIGNERNJ_BUILD_TESTS=OFF -DWIGNERNJ_BUILD_CXX_TESTS=OFF \
    -DWIGNERNJ_BUILD_EXAMPLES=OFF -DCMAKE_C_FLAGS="-O3 $cpu_flags"
  cmake --build /workspace/wignernj-build --parallel "$jobs"
  cmake --install /workspace/wignernj-build
  prefixes+=("$prefix/dependencies/wignernj")
fi
cmake_prefix=$(IFS=';'; echo "${prefixes[*]}")
dbcsr_archive=/input/probe/dbcsr-latest.tar.gz
fypp_archive=/input/probe/fypp-3.2-py3-none-any.whl
printf '%s  %s\n' d963cac9cb79e04d82bedf0f4febce9cd06470f84c7d5a45f74645d11f8c4875 "$dbcsr_archive" | sha256sum --check --status
printf '%s  %s\n' ec9d6fd0e54529e7873732be642ea9098e06cc7a1cbe0eb7faee31be6c2267fa "$fypp_archive" | sha256sum --check --status
mkdir -p /workspace/dbcsr-source /workspace/fypp
tar --no-same-owner --strip-components=1 -xzf "$dbcsr_archive" -C /workspace/dbcsr-source
unzip -q "$fypp_archive" -d /workspace/fypp
dbcsr_args=(
  -S /workspace/dbcsr-source -B /workspace/dbcsr-build
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix/dependencies/dbcsr"
  -DCMAKE_INSTALL_LIBDIR=lib64 -DCMAKE_POSITION_INDEPENDENT_CODE=ON
  -DFYPP_EXECUTABLE=/workspace/fypp/fypp.py -DCMAKE_PREFIX_PATH="$cmake_prefix"
  -DUSE_MPI=ON -DUSE_MPI_F08=ON -DUSE_OPENMP=ON -DUSE_LIBXS=ON -DUSE_LIBXSMM=ON
  -Dlibxsmm_DIR="$prefix/share/sai/cmake" -DWITH_EXAMPLES=OFF
  -DCMAKE_C_FLAGS="-O3 $cpu_flags" -DCMAKE_Fortran_FLAGS="-O3 $cpu_flags"
  -DCMAKE_CXX_FLAGS="-O3 $cpu_flags -Wno-error=deprecated-declarations"
)
if [[ "$accel" == CUDA ]]; then
  dbcsr_args+=(-DUSE_ACCEL=cuda -DWITH_GPU=V100 -DCMAKE_CUDA_ARCHITECTURES="$gpu_arch")
else
  dbcsr_args+=(-DUSE_ACCEL=none)
fi
cmake "${dbcsr_args[@]}"
cmake --build /workspace/dbcsr-build --parallel "$jobs"
cmake --install /workspace/dbcsr-build
mkdir -p /workspace/build "$prefix/bin" "$prefix/share/sai"
cmake_args=(
  -S /workspace/source -B /workspace/build -DCMAKE_BUILD_TYPE=Release
  -DCMAKE_INSTALL_PREFIX="$prefix" -DCMAKE_INSTALL_LIBDIR=lib64
  -DCMAKE_LIBRARY_PATH="$cuda_libraries"
  -DCMAKE_PREFIX_PATH="$cmake_prefix" -DDBCSR_DIR="$dbcsr"
  -DLibint2_DIR="$prefix/share/sai/cmake" -Dlibxsmm_DIR="$prefix/share/sai/cmake"
  -DCP2K_ELPA_ROOT="$ELPA_ROOT" -DCP2K_ENABLE_ELPA_OPENMP_SUPPORT=ON
  -DCP2K_FFTW3_ROOT="$FFTW_ROOT" -DCP2K_BLAS_VENDOR=OpenBLAS
  -DCMAKE_INSTALL_RPATH='$ORIGIN;$ORIGIN/../lib64;$ORIGIN/../lib'
  -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=FALSE -DCMAKE_BUILD_WITH_INSTALL_RPATH=FALSE
  -DCP2K_USE_MPI=ON -DCP2K_USE_MPI_F08=ON -DCP2K_USE_ACCEL="$accel"
  -DCP2K_USE_DBCSR_CONFIG=ON -DCP2K_USE_FFTW3=ON -DCP2K_USE_LIBXC=ON
  -DCP2K_USE_LIBINT2=ON -DCP2K_USE_ELPA=ON -DCP2K_USE_COSMA=ON
  -DCP2K_USE_LIBXS=ON -DCP2K_USE_LIBXSMM=ON -DCP2K_USE_PLUMED=ON
  -DCP2K_USE_SPGLIB=ON -DCP2K_USE_VORI=ON -DCP2K_USE_HDF5=ON
  -DHDF5_ROOT="$hdf5_root" -DHDF5_PREFER_PARALLEL=ON
  -DCP2K_USE_DFTD4=ON -DCP2K_USE_TBLITE=ON
  -DCMAKE_Fortran_FLAGS="-O3 $cpu_flags" -DCMAKE_C_FLAGS="-O3 $cpu_flags"
  -DCMAKE_CXX_FLAGS="-O3 $cpu_flags"
)
if [[ "$accel" == CUDA ]]; then
  cmake_args+=(
    -DCP2K_WITH_GPU=V100 -DCP2K_USE_CUDA=ON
    -DCMAKE_CUDA_ARCHITECTURES="$gpu_arch" -DCP2K_USE_CUSOLVER_MP=ON
    -DCP2K_CUSOLVER_MP_ROOT=/opt/devtools/nvidia/mp_libs
    -DCP2K_NCCL_ROOT="${NCCL_ROOT:?NCCL module root required}"
  )
else
  cmake_args+=( -DCP2K_USE_CUSOLVER_MP=OFF )
fi
cmake "${cmake_args[@]}"
python3 /control/cp2k_feature_contract.py cache "$prefix" "$target" "$source_sha"
cmake --build /workspace/build --parallel "$jobs"
cmake --install /workspace/build

# Keep libraries at their build-time private Spack paths, including the
# independent libplumedKernel.so. Never relocate by copying just libplumed.so.
cp /results/install-success.json "$prefix/share/sai/spack-dependencies.json"
cp /results/libint-export-repair.json "$prefix/share/sai/libint-export-repair.json"

printf '%s\n' "$source_sha" > "$prefix/share/sai/source-sha"
printf '%s\n' "$target" > "$prefix/share/sai/target"
module -t list > "$prefix/share/sai/modules.txt" 2>&1 || true
cp /workspace/build/CMakeCache.txt "$prefix/share/sai/CMakeCache.txt"
cp /control/cp2k_feature_contract.py "$prefix/share/sai/cp2k_feature_contract.py"
runtime_ld="$prefix/lib64:$prefix/lib:$prefix/dependencies/libxs/lib:$tblite_root/lib:$cp2k_spack_runtime_ld:/usr/lib/x86_64-linux-gnu/blas:/usr/lib/x86_64-linux-gnu/lapack"
IFS=: read -ra inherited_ld <<< "${LD_LIBRARY_PATH:-}"
for entry in "${inherited_ld[@]}"; do
  [[ -n "$entry" ]] && runtime_ld+=":$entry"
done
for dependency in "${prefixes[@]}"; do
  runtime_ld+=":$dependency/lib:$dependency/lib64"
done
# Only runtime executables and absolute dependency paths are persisted. Build
# cmake/git/fypp directories and controller snapshots must never leak here.
printf 'export PATH=%q\nexport LD_LIBRARY_PATH=%q\nexport CP2K_DATA_DIR=%q\n' \
  "$prefix/bin:/usr/bin:/bin" "$runtime_ld" "$prefix/share/cp2k/data" > "$prefix/share/sai/runtime-env.sh"
printf '%s\n' "$SAI_BUILD_PARTITION" > "$prefix/share/sai/build-partition"
hostname > "$prefix/share/sai/build-hostname"
lscpu > "$prefix/share/sai/build-lscpu.txt"
gcc -Q -march=native -mtune=native --help=target > "$prefix/share/sai/native-compiler-target.txt"
sha256sum "$libxs_archive" > "$prefix/share/sai/dependency-archives.sha256"
sha256sum "$dbcsr_archive" "$fypp_archive" >> "$prefix/share/sai/dependency-archives.sha256"
cp /workspace/tblite-build/source-archives.sha256 "$prefix/share/sai/tblite-source-archives.sha256"
python3 /control/cp2k_feature_contract.py source-changes "$prefix" "$target" "$source_sha"
chmod 0555 "$prefix/bin/cp2k.psmp"
export LD_LIBRARY_PATH="$runtime_ld"
ldd "$prefix/bin/cp2k.psmp" | tee "$prefix/share/sai/ldd.txt"
! grep -q 'not found' "$prefix/share/sai/ldd.txt"
