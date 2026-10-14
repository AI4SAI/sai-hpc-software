#!/usr/bin/env bash
# Source only inside the file-backed build overlay after environment.sh.
# System ELPA/MPI/BLAS/CUDA remain read-only module dependencies. Optional
# site archives are SHA-256 pinned; no network or on-host source install.
set -euo pipefail
: "${INSTALL_PREFIX:?}" "${BUILD_JOBS:?}"
[[ "$INSTALL_PREFIX" == /opt/apps/abacus/abacus-*/* ]]
python3 /control/abacus_dependencies.py unpack
deps="$INSTALL_PREFIX/dependencies"
mkdir -p "$deps" "$INSTALL_PREFIX/share/sai"
cp /workspace/dependencies/dependency-lock.json "$INSTALL_PREFIX/share/sai/"

# ABACUS 3.11 requires exported cereal/RapidJSON CONFIG targets, not
# the header hints accepted by the old preinstalled ABACUS recipe.
# GNUInstallDirs can choose lib64 on the site image. Pin the layout consumed
# below instead of relying on the distribution's default library directory.
cmake -S /workspace/dependencies/cereal-master -B /workspace/dependency-build/cereal \
  -DCMAKE_INSTALL_PREFIX="$deps/cereal" -DCMAKE_INSTALL_LIBDIR=lib -DJUST_INSTALL_CEREAL=ON \
  -DBUILD_TESTS=OFF -DBUILD_SANDBOX=OFF -DBUILD_DOC=OFF
cmake --install /workspace/dependency-build/cereal
test -s "$deps/cereal/lib/cmake/cereal/cerealConfig.cmake"
test -s "$deps/cereal/lib/cmake/cereal/cerealTargets.cmake"
json_backend=$(python3 /control/abacus_dependencies.py json-backend)
if [[ "$json_backend" == nlohmann ]]; then
cmake -S /workspace/dependencies/json-3.12.0 -B /workspace/dependency-build/json \
  -DCMAKE_INSTALL_PREFIX="$deps/json" -DCMAKE_INSTALL_LIBDIR=lib \
  -DJSON_BuildTests=OFF -DJSON_Install=ON -DJSON_MultipleHeaders=ON
cmake --install /workspace/dependency-build/json
test -s "$deps/json/share/cmake/nlohmann_json/nlohmann_jsonConfig.cmake"
abacus_json_options=(-DENABLE_JSON=ON "-Dnlohmann_json_DIR=$deps/json/share/cmake/nlohmann_json")
elif [[ "$json_backend" == rapidjson ]]; then
cmake -S /workspace/dependencies/rapidjson-master -B /workspace/dependency-build/rapidjson \
  -DCMAKE_INSTALL_PREFIX="$deps/rapidjson" -DCMAKE_INSTALL_LIBDIR=lib -DRAPIDJSON_BUILD_DOC=OFF \
  -DRAPIDJSON_BUILD_EXAMPLES=OFF -DRAPIDJSON_BUILD_TESTS=OFF
cmake --install /workspace/dependency-build/rapidjson
# This archive installs its build-tree config last; keep the install-tree
# variant so exported development metadata does not name /workspace.
cp /workspace/dependency-build/rapidjson/CMakeFiles/RapidJSONConfig.cmake \
  "$deps/rapidjson/lib/cmake/RapidJSON/RapidJSONConfig.cmake"
test -s "$deps/rapidjson/lib/cmake/RapidJSON/RapidJSONConfig.cmake"
abacus_json_options=(-DENABLE_RAPIDJSON=ON "-DRapidJSON_DIR=$deps/rapidjson/lib/cmake/RapidJSON")
else
  echo "unsupported JSON backend: $json_backend" >&2; exit 1
fi
for item in LibRI-b0eff7a LibComm-master libnpy-1.0.1; do
  mkdir -p "$deps/$item"
  cp -a "/workspace/dependencies/$item/include" "$deps/$item/"
done
full_profile=$(python3 -c 'from pathlib import Path; import json; print(int(bool(json.loads(Path("/workspace/dependencies/dependency-lock.json").read_text()).get("full_features"))))')
if [[ "$full_profile" == 1 ]]; then
  source /control/abacus_full_dependencies.sh
else
  cp -a /workspace/dependencies/libtorch "$deps/libtorch"
  grep -q '_GLIBCXX_USE_CXX11_ABI=1' "$deps/libtorch/share/cmake/Torch/TorchConfig.cmake"
  abacus_ml_options=("-DTorch_DIR=$deps/libtorch/share/cmake/Torch")
fi
for package in cereal-master rapidjson-master json-3.12.0 LibRI-b0eff7a LibComm-master libnpy-1.0.1 NEP_CPU-main libtorch; do
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
  -DENABLE_LCAO=ON -DENABLE_LIBRI=ON -DENABLE_MLALGO=ON
  "${abacus_json_options[@]}"
  "-DELPA_DIR=${ELPA_ROOT:?system ELPA module required}"
  "-DLIBRI_DIR=$deps/LibRI-b0eff7a" "-DLIBCOMM_DIR=$deps/LibComm-master"
  "-Dcereal_DIR=$deps/cereal/lib/cmake/cereal"
  "${abacus_ml_options[@]}"
  "-Dlibnpy_INCLUDE_DIR=$deps/libnpy-1.0.1/include" "-DNEP_DIR=$deps/nep"
  '-DCMAKE_INSTALL_RPATH=$ORIGIN/../dependencies/libtorch/lib;$ORIGIN/../dependencies/nep/lib'
  -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=OFF -DCMAKE_BUILD_RPATH_USE_ORIGIN=ON
  -DFETCHCONTENT_FULLY_DISCONNECTED=ON -DFETCHCONTENT_UPDATES_DISCONNECTED=ON)
if [[ "$full_profile" == 1 ]]; then
  abacus_dependency_options+=("-DCMAKE_INSTALL_RPATH=\$ORIGIN/../lib;\$ORIGIN/../dependencies/nep/lib;$full_deps/lib;$MPI_HOME/lib;$ml_root/lib")
fi
