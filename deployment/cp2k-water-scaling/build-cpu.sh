#!/usr/bin/env bash
# Executed inside a container; install and work paths are identical on the host.
set -eo pipefail
run=${1:?run}; partition=${2:?partition}; original=${3:?accepted GPU prefix}
export PATH=/usr/bin:/bin LD_LIBRARY_PATH= LD_PRELOAD= PYTHONDONTWRITEBYTECODE=1
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/devtools
module load cmake/3.31.6 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto fftw/3.3.10 libxc/7.0.0-auto saiblas/2603-gnu-auto
module unload gcc
module load gcc/14.3.0 spglib/2.7.0
set -u
case "$partition" in
  4V100) isa=avx512 ;;
  16V100) isa=avx2 ;;
  *) exit 2 ;;
esac
[[ "$MPI_HOME" == *-"$isa" && "$OPENBLAS_ROOT" == *-"$isa" ]]
export CC=/opt/devtools/gcc/14.3.0/bin/gcc CXX=/opt/devtools/gcc/14.3.0/bin/g++ FC=/opt/devtools/gcc/14.3.0/bin/gfortran
export OMPI_CC=$CC OMPI_CXX=$CXX OMPI_FC=$FC
[[ "$($CC -dumpfullversion)" == 14.3.0 && "$($FC -dumpfullversion)" == 14.3.0 ]]
work=$run/work; prefix=$run/install; control=$run/control/controller
export TMPDIR=$run/runtime
export PRTE_MCA_prte_tmpdir_base=$TMPDIR OMPI_MCA_orte_tmpdir_base=$TMPDIR
source_sha=67b5da876dd6a76b8b021d5a04d1c81ba79a4c50
repository=/home/stardust/sai-hpc-software/cache/repositories/cp2k
cache=/home/stardust/sai-hpc-software/cache
mkdir -p "$work/source" "$prefix/share/sai" "$work/adapters" "$work/libxs-source" "$work/dbcsr-source" "$work/fypp"
git init "$work/source"
printf '%s\n' "$repository/objects" > "$work/source/.git/objects/info/alternates"
printf '%s\n' "$source_sha" > "$work/source/.git/shallow"
git -C "$work/source" -c core.hooksPath=/dev/null checkout --detach "$source_sha"
[[ "$(git -C "$work/source" rev-parse HEAD)" == "$source_sha" ]]
python3 "$control/cp2k_source_fixes.py" "$work/source" "$prefix/share/sai"
python3 "$run/control/deployment/cp2k-water-scaling/fix-redistribution.py" \
  "$work/source/src/fm/cp_fm_diag_utils.F" "$work/source/src/fm/cp_fm_diag_utils.F" \
  > "$prefix/share/sai/redistribution-repair.json"
python3 "$control/cp2k_spack_link.py" "$run/input/dependencies.json" "$partition" "$original" > "$work/spack-env.sh"
source "$work/spack-env.sh"
cp "$control/cp2k_Libint2Config.cmake" "$work/adapters/Libint2Config.cmake"
cp "$control/cp2k_libxsmmConfig.cmake" "$work/adapters/libxsmmConfig.cmake"
compilers=(-DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX" -DCMAKE_Fortran_COMPILER="$FC")
flags=(-DCMAKE_C_FLAGS='-O3 -march=native -mtune=native'
       -DCMAKE_CXX_FLAGS='-O3 -march=native -mtune=native'
       -DCMAKE_Fortran_FLAGS='-O3 -march=native -mtune=native')
printf '%s  %s\n' de26f50cb986a2f0e4f92c0eb489d40a44f7e4c5acd22751a6cfa2829dabd04d "$cache/cp2k-dependencies/libxs-1.0.0.tar.gz" | sha256sum -c
printf '%s  %s\n' d963cac9cb79e04d82bedf0f4febce9cd06470f84c7d5a45f74645d11f8c4875 "$cache/cp2k-probe/dbcsr-latest.tar.gz" | sha256sum -c
printf '%s  %s\n' ec9d6fd0e54529e7873732be642ea9098e06cc7a1cbe0eb7faee31be6c2267fa "$cache/cp2k-probe/fypp-3.2-py3-none-any.whl" | sha256sum -c
tar --no-same-owner --strip-components=1 -xzf "$cache/cp2k-dependencies/libxs-1.0.0.tar.gz" -C "$work/libxs-source"
tar --no-same-owner --strip-components=1 -xzf "$cache/cp2k-probe/dbcsr-latest.tar.gz" -C "$work/dbcsr-source"
unzip -oq "$cache/cp2k-probe/fypp-3.2-py3-none-any.whl" -d "$work/fypp"
cmake -S "$work/libxs-source" -B "$work/libxs-build" "${compilers[@]}" "${flags[@]}" \
  -DCMAKE_INSTALL_PREFIX="$prefix/dependencies/libxs" -DCMAKE_INSTALL_LIBDIR=lib \
  -DCMAKE_BUILD_TYPE=Release -DLIBXS_FORTRAN=ON -DCMAKE_POSITION_INDEPENDENT_CODE=ON
cmake --build "$work/libxs-build" --parallel "$BUILD_JOBS"
cmake --install "$work/libxs-build"
cmake_prefix="$OPENBLAS_ROOT;$LIBXC_ROOT;$FFTW_ROOT;$CP2K_LIBINT_ROOT;$CP2K_LIBXSMM_ROOT;$prefix/dependencies/libxs;$SPGLIB_ROOT"
cmake -S "$work/dbcsr-source" -B "$work/dbcsr-build" "${compilers[@]}" "${flags[@]}" \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix/dependencies/dbcsr" \
  -DCMAKE_INSTALL_LIBDIR=lib64 -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
  -DFYPP_EXECUTABLE="$work/fypp/fypp.py" -DCMAKE_PREFIX_PATH="$cmake_prefix" \
  -DUSE_MPI=ON -DUSE_MPI_F08=ON -DUSE_OPENMP=ON -DUSE_LIBXS=ON -DUSE_LIBXSMM=ON \
  -Dlibxsmm_DIR="$work/adapters" -DWITH_EXAMPLES=OFF -DUSE_ACCEL=none
cmake --build "$work/dbcsr-build" --parallel "$BUILD_JOBS"
cmake --install "$work/dbcsr-build"
cmake -S "$work/source" -B "$work/cp2k-build" "${compilers[@]}" "${flags[@]}" \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix" -DCMAKE_INSTALL_LIBDIR:STRING=lib64 \
  -DCMAKE_PREFIX_PATH="$cmake_prefix" -DDBCSR_DIR="$prefix/dependencies/dbcsr/lib64/cmake/dbcsr" \
  -DLibint2_DIR="$work/adapters" -Dlibxsmm_DIR="$work/adapters" \
  -DCP2K_FFTW3_ROOT="$FFTW_ROOT" -DCP2K_BLAS_VENDOR=OpenBLAS \
  -DCMAKE_INSTALL_RPATH='$ORIGIN;$ORIGIN/../lib64;$ORIGIN/../lib' \
  -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=FALSE -DCMAKE_BUILD_WITH_INSTALL_RPATH=FALSE \
  -DCP2K_USE_MPI=ON -DCP2K_USE_MPI_F08=ON -DCP2K_USE_DBCSR_CONFIG=ON \
  -DCP2K_USE_ACCEL=NONE -DCP2K_DBCSR_USE_CPU_ONLY=ON -DCP2K_USE_CUSOLVER_MP=OFF \
  -DCP2K_USE_ELPA=OFF -DCP2K_USE_COSMA=OFF -DCP2K_USE_SPLA=OFF -DCP2K_USE_SPLA_GEMM_OFFLOADING=OFF \
  -DCP2K_USE_FFTW3=ON -DCP2K_USE_LIBXC=ON -DCP2K_USE_LIBINT2=ON \
  -DCP2K_USE_LIBXS=ON -DCP2K_USE_LIBXSMM=ON -DCP2K_USE_SPGLIB=ON
cmake --build "$work/cp2k-build" --parallel "$BUILD_JOBS"
cmake --install "$work/cp2k-build"
cp "$work/cp2k-build/CMakeCache.txt" "$prefix/share/sai/"
cp "$work/dbcsr-build/CMakeCache.txt" "$prefix/share/sai/DBCSR-CMakeCache.txt"
printf '%s\n' "$source_sha" > "$prefix/share/sai/source-sha"
module -t list > "$prefix/share/sai/modules.txt" 2>&1
export LD_LIBRARY_PATH="$prefix/lib64:$prefix/dependencies/dbcsr/lib64:$prefix/dependencies/libxs/lib:$cp2k_spack_runtime_ld:${LD_LIBRARY_PATH:-}"
ldd "$prefix/bin/cp2k.psmp" > "$prefix/share/sai/ldd.txt"
if grep -E 'not found|libcusolver|libcublas|libcufft|libelpa|libspla|libcosma' "$prefix/share/sai/ldd.txt"; then exit 1; fi
CUDA_VISIBLE_DEVICES= OMP_NUM_THREADS=1 "$prefix/bin/cp2k.psmp" --version > "$prefix/share/sai/version.txt"
cat "$prefix/share/sai/version.txt"
touch "$run/results/build-complete"
echo CPU_BUILD_COMPLETE_SCIENCE_ACCEPTANCE_PENDING
