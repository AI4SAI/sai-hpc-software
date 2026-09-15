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
case "${MPI_HOME:?}" in
  *-avx512) site=/opt/apps/cp2k/cp2k-2026.1-avx512/tools/toolchain/install ;;
  *-avx2) site=/opt/apps/cp2k/cp2k-2026.1-avx2/tools/toolchain/install ;;
  *) echo "unknown native dependency ISA: $MPI_HOME" >&2; exit 1 ;;
esac
suffix=; [[ "$accel" == CUDA ]] && suffix=-cuda
dbcsr="$prefix/dependencies/dbcsr/lib64/cmake/dbcsr"
[[ "${ELPA_ROOT:?}" == /opt/devtools/elpa/elpa-2026.02.001-2603-gnu/* ]]
if [[ "$target" == 8v100v0-avx512 ]]; then
  # Gold 6146 supports native AVX-512 but lacks VNNI; site dependencies use AVX2.
  [[ "${OPAL_PREFIX:?}" == *-avx2 ]]
  [[ "${OPENBLAS_ROOT:?}" == *-avx2 ]]
fi

new_deps=/input/dependencies/tblite-dependencies.tar.gz
[[ -s "$new_deps" ]] || { echo "verified tblite/DFT-D4 dependency bundle is required" >&2; exit 1; }
printf '%s  %s\n' a2f54e22b9397841cb414696abbcd001d94b3599eb6aeabe222d0dd4e8e69a52 "$new_deps" | sha256sum --check --status
tblite_root="$prefix/dependencies/tblite"
# The successful 4V100 bundle establishes compatible source versions, not
# permission to reuse its own compiled libraries across ISA targets. Rebuild
# its seven locked sources natively inside each target's overlay.
export CP2K_NATIVE_FLAGS="-O3 $cpu_flags"
bash /control/cp2k_dependencies.sh /input/probe /workspace/tblite-build "$tblite_root"

# The preinstalled CP2K toolchain carries a serial HDF5, which does not
# provide h5pset_fapl_mpio_f even though CP2K's HDF5 interface is enabled.
# Rebuild this pinned source with the active, ISA-matched OpenMPI wrapper and
# keep the result under the target prefix so the final artifact is self-contained.
hdf5_archive="$site/../build/hdf5-1.14.6.tar.gz"
[[ -s "$hdf5_archive" ]] || { echo "pinned parallel HDF5 source is required: $hdf5_archive" >&2; exit 1; }
hdf5_root="$prefix/dependencies/hdf5"
rm -rf /workspace/hdf5-source /workspace/hdf5-build
mkdir -p /workspace/hdf5-source
# This site archive records paths as ./hdf5-1.14.6/..., so remove both the
# leading ./ component and the source-directory component.
tar --no-same-owner --strip-components=2 -xzf "$hdf5_archive" -C /workspace/hdf5-source
pushd /workspace/hdf5-source >/dev/null
[[ -x ./configure ]]
# The clean container binds /usr without /etc/alternatives.  On these nodes
# /usr/bin/awk may therefore be a dangling alternatives symlink even though
# mawk is installed.  HDF5's configure uses awk to materialize its Fortran
# KIND declarations; keep this repair local to the configure invocation.
if ! command -v awk >/dev/null 2>&1 || ! awk 'BEGIN { exit 0 }' >/dev/null 2>&1; then
  hdf5_tools=/workspace/hdf5-tools
  [[ -x /usr/bin/mawk ]] || { echo "HDF5 configure requires awk or mawk" >&2; exit 1; }
  mkdir -p "$hdf5_tools"
  ln -sfn -- /usr/bin/mawk "$hdf5_tools/awk"
  export PATH="$hdf5_tools:$PATH"
  hash -r
fi
awk 'BEGIN { exit 0 }' >/dev/null
CC=mpicc CXX=mpicxx FC=mpifort ./configure \
  --prefix="$hdf5_root" --enable-parallel --enable-fortran \
  --disable-shared --enable-static --disable-hl \
  CFLAGS="-O3 -fPIC $cpu_flags" CXXFLAGS="-O3 -fPIC $cpu_flags" FCFLAGS="-O3 -fPIC $cpu_flags" \
  --cache-file=/workspace/hdf5-config.cache 2>&1 | tee /workspace/hdf5-configure.log
make -j"$jobs"
make install
popd >/dev/null
grep -q '^                     Parallel HDF5: yes$' "$hdf5_root/lib/libhdf5.settings"

prefixes=(
  "$ELPA_ROOT" "$OPENBLAS_ROOT" "$LIBXC_ROOT" "$FFTW_ROOT" "$tblite_root"
  "$site/COSMA-2.7.0$suffix" "$site/SpLA-1.6.1$suffix"
  "$site/libint-v2.6.0-cp2k-lmax-5"
  "$site/libxsmm-e0c4a2389afba36c453233ad7de07bd92c715bec" "$site/spglib-2.5.0"
  "$site/libvori-220621" "$hdf5_root" "$site/plumed-2.9.3"
)
for dependency in "${prefixes[@]}"; do
  [[ -d "$dependency" ]] || { echo "required dependency missing: $dependency" >&2; exit 1; }
  export PKG_CONFIG_PATH="${PKG_CONFIG_PATH:+$PKG_CONFIG_PATH:}$dependency/lib/pkgconfig:$dependency/lib64/pkgconfig"
done
# The site Libint2/LIBXSMM installations have pkg-config metadata but lack
# modern CMake CONFIG packages. Trusted adapters name the real site libraries;
# they neither rebuild nor copy the host installation into the image.
export CP2K_SITE_DEPENDENCIES="$site"
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

printf '%s\n' "$source_sha" > "$prefix/share/sai/source-sha"
printf '%s\n' "$target" > "$prefix/share/sai/target"
module -t list > "$prefix/share/sai/modules.txt" 2>&1 || true
cp /workspace/build/CMakeCache.txt "$prefix/share/sai/CMakeCache.txt"
cp /control/cp2k_feature_contract.py "$prefix/share/sai/cp2k_feature_contract.py"
runtime_ld="$prefix/lib64:$prefix/lib:$prefix/dependencies/libxs/lib:$tblite_root/lib:/usr/lib/x86_64-linux-gnu/blas:/usr/lib/x86_64-linux-gnu/lapack"
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
sha256sum "$new_deps" "$libxs_archive" > "$prefix/share/sai/dependency-archives.sha256"
sha256sum "$dbcsr_archive" "$fypp_archive" >> "$prefix/share/sai/dependency-archives.sha256"
cp /workspace/tblite-build/source-archives.sha256 "$prefix/share/sai/tblite-source-archives.sha256"
python3 /control/cp2k_feature_contract.py source-changes "$prefix" "$target" "$source_sha"
chmod 0555 "$prefix/bin/cp2k.psmp"
export LD_LIBRARY_PATH="$runtime_ld"
ldd "$prefix/bin/cp2k.psmp" | tee "$prefix/share/sai/ldd.txt"
! grep -q 'not found' "$prefix/share/sai/ldd.txt"
