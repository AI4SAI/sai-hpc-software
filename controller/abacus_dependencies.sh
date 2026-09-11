#!/usr/bin/env bash
# Source only inside the file-backed build overlay after environment.sh.
# System ELPA/MPI/BLAS/CUDA remain read-only module dependencies. Optional
# site archives are SHA-256 pinned; no network or on-host source install.
set -euo pipefail
: "${INSTALL_PREFIX:?}" "${BUILD_JOBS:?}"
[[ "$INSTALL_PREFIX" == /opt/software/abacus/* ]]
python3 /control/abacus_dependencies.py unpack
deps="$INSTALL_PREFIX/dependencies"
mkdir -p "$deps" "$INSTALL_PREFIX/share/sai"
cp /workspace/dependencies/dependency-lock.json "$INSTALL_PREFIX/share/sai/"

# CMake 3.11 ABACUS requires exported cereal/RapidJSON CONFIG targets, not
# the header hints accepted by the old preinstalled ABACUS recipe.
cmake -S /workspace/dependencies/cereal-master -B /workspace/dependency-build/cereal \
  -DCMAKE_INSTALL_PREFIX="$deps/cereal" -DJUST_INSTALL_CEREAL=ON \
  -DBUILD_TESTS=OFF -DBUILD_SANDBOX=OFF -DBUILD_DOC=OFF
cmake --install /workspace/dependency-build/cereal
cmake -S /workspace/dependencies/rapidjson-master -B /workspace/dependency-build/rapidjson \
  -DCMAKE_INSTALL_PREFIX="$deps/rapidjson" -DRAPIDJSON_BUILD_DOC=OFF \
  -DRAPIDJSON_BUILD_EXAMPLES=OFF -DRAPIDJSON_BUILD_TESTS=OFF
cmake --install /workspace/dependency-build/rapidjson
# This archive installs its build-tree config last; keep the install-tree
# variant so exported development metadata does not name /workspace.
cp /workspace/dependency-build/rapidjson/CMakeFiles/RapidJSONConfig.cmake \
  "$deps/rapidjson/lib/cmake/RapidJSON/RapidJSONConfig.cmake"
for item in LibRI-master LibComm-master libnpy-1.0.1; do
  mkdir -p "$deps/$item"
  cp -a "/workspace/dependencies/$item/include" "$deps/$item/"
done
cp -a /workspace/dependencies/libtorch "$deps/libtorch"
grep -q '_GLIBCXX_USE_CXX11_ABI=1' "$deps/libtorch/share/cmake/Torch/TorchConfig.cmake"
for package in cereal-master rapidjson-master LibRI-master LibComm-master libnpy-1.0.1 NEP_CPU-main libtorch; do
  mkdir -p "$INSTALL_PREFIX/share/licenses/$package"
  for license in /workspace/dependencies/"$package"/{LICENSE*,COPYING*,NOTICE*}; do
    if [[ -f "$license" ]]; then cp "$license" "$INSTALL_PREFIX/share/licenses/$package/"; fi
  done
done

# The site's NEP DSO has no SONAME and records another ABACUS install in
# RUNPATH. Recompile its locked source on this partition instead of inheriting
# its ISA or absolute dependencies. SONAME keeps DT_NEEDED relocatable.
mkdir -p "$deps/nep/lib" "$deps/nep/include"
cp /workspace/dependencies/NEP_CPU-main/src/*.h "$deps/nep/include/"
g++ -O3 -DNDEBUG -fPIC -std=c++11 -march=native -mtune=native \
  -I/workspace/dependencies/NEP_CPU-main/src -shared \
  /workspace/dependencies/NEP_CPU-main/src/nep.cpp \
  /workspace/dependencies/NEP_CPU-main/src/ewald_nep.cpp \
  /workspace/dependencies/NEP_CPU-main/src/neighbor_nep.cpp \
  -Wl,-soname,libnep.so '-Wl,-rpath,$ORIGIN' -o "$deps/nep/lib/libnep.so"

# An array is consumed by the ABACUS recipe; no eval or build-path runtime env.
abacus_dependency_options=(
  -DENABLE_LCAO=ON -DENABLE_LIBRI=ON -DENABLE_MLALGO=ON -DENABLE_RAPIDJSON=ON
  "-DELPA_DIR=${ELPA_ROOT:?system ELPA module required}"
  "-DLIBRI_DIR=$deps/LibRI-master" "-DLIBCOMM_DIR=$deps/LibComm-master"
  "-Dcereal_DIR=$deps/cereal/lib/cmake/cereal"
  "-DRapidJSON_DIR=$deps/rapidjson/lib/cmake/RapidJSON"
  "-DTorch_DIR=$deps/libtorch/share/cmake/Torch"
  "-Dlibnpy_INCLUDE_DIR=$deps/libnpy-1.0.1/include" "-DNEP_DIR=$deps/nep"
  '-DCMAKE_INSTALL_RPATH=$ORIGIN/../dependencies/libtorch/lib;$ORIGIN/../dependencies/nep/lib'
  -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=OFF -DCMAKE_BUILD_RPATH_USE_ORIGIN=ON
  -DFETCHCONTENT_FULLY_DISCONNECTED=ON -DFETCHCONTENT_UPDATES_DISCONNECTED=ON)
