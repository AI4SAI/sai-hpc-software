#!/usr/bin/env bash
# Sourced only inside the offline overlay; every install uses the final prefix.
set -euo pipefail
full_deps="$deps/full"
full_src=/workspace/dependencies
full_build=/workspace/dependency-build/full
ml_root=/opt/apps/conda_env/deepmd-kit-3.1.2
test -s "$ml_root/include/deepmd/c_api.h"
test -s "$ml_root/include/tensorflow/core/public/session.h"
test -s "$ml_root/share/cmake/Torch/TorchConfig.cmake"
test -s "$ml_root/lib/libtensorflow_cc.so.2"
test -s "$ml_root/lib/libdeepmd_c.so"
source /control/abacus_site_gpu.sh
mkdir -p "$full_deps" "$full_build"
python3 /control/abacus_dependencies.py repair-dftd4-cmake \
  --source "$full_src/dftd4-4.2.0"

full_common=(-DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$full_deps"
  -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_POSITION_INDEPENDENT_CODE=ON
  -DCMAKE_C_COMPILER=mpicc -DCMAKE_CXX_COMPILER=mpicxx -DCMAKE_Fortran_COMPILER=mpifort
  -DCMAKE_C_FLAGS=-march=native -DCMAKE_CXX_FLAGS=-march=native
  -DCMAKE_Fortran_FLAGS=-march=native -DBUILD_SHARED_LIBS=OFF
  "-DCMAKE_PREFIX_PATH=$full_deps" "-DCMAKE_INSTALL_RPATH=$full_deps/lib"
  -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=OFF -DFETCHCONTENT_FULLY_DISCONNECTED=ON)
full_build_install() {
  local name=$1 source_dir=$2
  shift 2
  cmake -S "$source_dir" -B "$full_build/$name" "${full_common[@]}" "$@"
  if [[ "$name" == pexsi ]]; then
    cmake --build "$full_build/$name" --target pexsi --parallel "$BUILD_JOBS"
  else
    cmake --build "$full_build/$name" --parallel "$BUILD_JOBS"
  fi
  cmake --install "$full_build/$name"
}

# jonquil's release CMake defaults ENABLE_TESTING to ON and therefore asks
# for test-drive even when the parent build is not running its tests.  The
# source is bundled in the DFT-D4 archive, so install its local CMake package
# into the same prefix before configuring the other bundled dependencies.
full_build_install test-drive "$full_src/dftd4-4.2.0/subprojects/test-drive" \
  -DBUILD_TESTING=OFF -DWITH_QP=OFF -DWITH_XDP=OFF
test -s "$full_deps/lib/cmake/test-drive/test-drive-config.cmake"

# The official ParMETIS release bundles its matching METIS/GKlib sources.
full_build_install parmetis "$full_src/parmetis-4.0.3" \
  -DSHARED=OFF -DMETIS_INSTALL=ON -DMETIS_LIBRARY_TYPE=STATIC \
  "-DGKLIB_PATH=$full_src/parmetis-4.0.3/metis/GKlib" \
  "-DMETIS_PATH=$full_src/parmetis-4.0.3/metis"
cp "$full_src/parmetis-4.0.3/metis/include/metis.h" "$full_deps/include/"
test -s "$full_deps/lib/libmetis.a"
test -s "$full_deps/lib/libparmetis.a"
full_build_install superlu "$full_src/superlu_dist-7.2.0" \
  -DTPL_ENABLE_INTERNAL_BLASLIB=OFF "-DTPL_BLAS_LIBRARIES=$OPENBLAS_ROOT/lib/libopenblas.so" \
  -DTPL_ENABLE_PARMETISLIB=ON "-DTPL_PARMETIS_INCLUDE_DIRS=$full_deps/include" \
  "-DTPL_PARMETIS_LIBRARIES=$full_deps/lib/libparmetis.a;$full_deps/lib/libmetis.a" \
  -DTPL_ENABLE_COMBBLASLIB=OFF -DTPL_ENABLE_LAPACKLIB=OFF \
  -Denable_tests=OFF -Denable_examples=OFF
full_build_install pexsi "$full_src/pexsi_v2.0.0" \
  -DPEXSI_ENABLE_FORTRAN=ON -DPEXSI_ENABLE_OPENMP=ON \
  "-DSuperLU_DIST_PREFIX=$full_deps" "-DParMETIS_PREFIX=$full_deps" \
  "-DMETIS_PREFIX=$full_deps" "-DBLAS_LIBRARIES=$OPENBLAS_ROOT/lib/libopenblas.so" \
  "-DLAPACK_LIBRARIES=$OPENBLAS_ROOT/lib/libopenblas.so"

# Build bundled release dependencies in dependency order, with only local
# CMake packages allowed. No Git/FetchContent access is available in the job.
for package in toml-f jonquil mctc-lib mstore multicharge; do
  full_build_install "$package" "$full_src/dftd4-4.2.0/subprojects/$package" \
    -DBUILD_TESTING=OFF -DENABLE_TESTING=OFF -DWITH_TESTS=OFF \
    "-D${package}-dependency-method=cmake" \
    "-DBLAS_LIBRARIES=$OPENBLAS_ROOT/lib/libopenblas.so" \
    "-DLAPACK_LIBRARIES=$OPENBLAS_ROOT/lib/libopenblas.so"
done
full_build_install dftd4 "$full_src/dftd4-4.2.0" \
  -DBUILD_TESTING=OFF -DWITH_TESTS=OFF -DWITH_API=ON \
  -Ddftd4-dependency-method=cmake \
  -DMCTCLIB_FIND_METHOD=cmake -DMSTORE_FIND_METHOD=cmake \
  -DMULTICHARGE_FIND_METHOD=cmake -DTEST_DRIVE_FIND_METHOD=cmake \
  "-DBLAS_LIBRARIES=$OPENBLAS_ROOT/lib/libopenblas.so" \
  "-DLAPACK_LIBRARIES=$OPENBLAS_ROOT/lib/libopenblas.so"
test -s "$full_deps/lib/cmake/PEXSI/PEXSIConfig.cmake"
test -s "$full_deps/lib/cmake/dftd4/dftd4-config.cmake"

# Deliberately do not load deepmd-kit/3.1.2: that legacy module activates
# Conda and prepends its MPI. Keep the site's MPI and compiler libraries first.
export LD_LIBRARY_PATH="$MPI_HOME/lib:${LD_LIBRARY_PATH:-}:$ml_root/lib:$full_deps/lib"
source /control/abacus_site_mpi.sh
export TORCH_CUDA_ARCH_LIST=7.0
abacus_ml_options=("${abacus_mpi_options[@]}" "-DTorch_DIR=$ml_root/share/cmake/Torch"
  "${abacus_gpu_options[@]}"
  "-DDeePMD_DIR=$ml_root" "-DTensorFlow_DIR=$ml_root"
  "-DProtobuf_DIR=$ml_root/lib/cmake/protobuf" "-DTORCH_CUDA_ARCH_LIST=7.0"
  -DENABLE_DFTD4=ON -DENABLE_PEXSI=ON -DENABLE_CNPY=ON
  -DENABLE_FLOAT_FFTW=ON -DENABLE_FFT_TWO_CENTER=ON
  "-DPEXSI_DIR=$full_deps/lib/cmake/PEXSI"
  "-Ddftd4_DIR=$full_deps/lib/cmake/dftd4"
  "-DFETCHCONTENT_SOURCE_DIR_CNPY=$full_src/cnpy-4e8810b1a8637695171ed346ce68f6984e585ef4"
  "-DSuperLU_DIST_PREFIX=$full_deps" "-DParMETIS_PREFIX=$full_deps" "-DMETIS_PREFIX=$full_deps"
  "-DCMAKE_EXE_LINKER_FLAGS=-Wl,-rpath-link,$MPI_HOME/lib -Wl,--no-as-needed $ml_root/lib/libdeepmd_c.so -Wl,--as-needed")
for package in parmetis-4.0.3 superlu_dist-7.2.0 pexsi_v2.0.0 dftd4-4.2.0 cnpy-4e8810b1a8637695171ed346ce68f6984e585ef4; do
  mkdir -p "$INSTALL_PREFIX/share/licenses/$package"
  for license in "$full_src/$package"/{LICENSE*,COPYING*,NOTICE*}; do
    if [[ -f "$license" ]]; then cp "$license" "$INSTALL_PREFIX/share/licenses/$package/"; fi
  done
done
