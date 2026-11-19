#!/usr/bin/env bash
# Run only inside the workflow's offline, file-backed build overlay.
set -euo pipefail
target=$1
python3 /control/abacus_dependencies.py unpack
deps="$INSTALL_PREFIX/dependencies"
mkdir -p "$deps" "$INSTALL_PREFIX/share/sai"
cp -a /workspace/dependencies/libtorch "$deps/libtorch"
grep -q '_GLIBCXX_USE_CXX11_ABI=1' "$deps/libtorch/share/cmake/Torch/TorchConfig.cmake"
cmake -S /workspace/dependencies/rapidjson-master -B /workspace/dependency-build/rapidjson \
  -DCMAKE_INSTALL_PREFIX="$deps/rapidjson" -DCMAKE_INSTALL_LIBDIR=lib \
  -DRAPIDJSON_BUILD_DOC=OFF -DRAPIDJSON_BUILD_EXAMPLES=OFF -DRAPIDJSON_BUILD_TESTS=OFF
cmake --install /workspace/dependency-build/rapidjson
cp /workspace/dependency-build/rapidjson/CMakeFiles/RapidJSONConfig.cmake \
  "$deps/rapidjson/lib/cmake/RapidJSON/RapidJSONConfig.cmake"

# LTS has the pre-3.11 CMake interface and CAL-based cuSOLVERMp backend.
opts=(-DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$INSTALL_PREFIX"
  -DCMAKE_CXX_COMPILER=mpicxx -DBUILD_TESTING=OFF -DGIT_SUBMODULE=OFF -DCOMMIT_INFO=OFF
  -DENABLE_MPI=ON -DUSE_OPENMP=ON -DENABLE_LCAO=ON -DUSE_ELPA=ON
  -DENABLE_LIBXC=ON -DENABLE_LIBRI=ON -DENABLE_LIBCOMM=ON
  -DENABLE_DEEPKS=ON -DENABLE_RAPIDJSON=ON -DENABLE_FLOAT_FFTW=ON
  -DENABLE_FFT_TWO_CENTER=ON -DENABLE_NATIVE_OPTIMIZATION=OFF
  -DCMAKE_CXX_STANDARD=17 -DCMAKE_CUDA_STANDARD=17
  '-DCMAKE_CXX_FLAGS=-march=native -mtune=native'
  "-DELPA_DIR=$ELPA_ROOT"
  -DCEREAL_INCLUDE_DIR=/workspace/dependencies/cereal-master/include
  -DLIBRI_DIR=/workspace/dependencies/LibRI-b0eff7a
  -DLIBCOMM_DIR=/workspace/dependencies/LibComm-master
  -Dlibnpy_INCLUDE_DIR=/workspace/dependencies/libnpy-1.0.1/include
  "-DTorch_DIR=$deps/libtorch/share/cmake/Torch"
  "-DRapidJSON_DIR=$deps/rapidjson/lib/cmake/RapidJSON"
  -DFETCHCONTENT_FULLY_DISCONNECTED=ON -DFETCHCONTENT_UPDATES_DISCONNECTED=ON
  -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=OFF -DCMAKE_BUILD_RPATH_USE_ORIGIN=ON)
# Keep a literal ELF $ORIGIN; the array below does not undergo another expansion.
runtime_rpath='$ORIGIN/../dependencies/libtorch/lib'
if [[ "$target" == dsprhbm ]]; then
  opts+=(-DUSE_CUDA=OFF -DENABLE_CUSOLVERMP=OFF)
else
  : "${LTS_MATH_ROOT:?CAL-compatible SDK required}"
  opts+=(-DUSE_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=70 -DENABLE_CUSOLVERMP=ON
    "-DCAL_CUSOLVERMP_PATH=$LTS_MATH_ROOT/lib"
    "-DCMAKE_CUDA_FLAGS=-I$LTS_MATH_ROOT/include")
  runtime_rpath+=";$LTS_MATH_ROOT/lib"
fi
opts+=("-DCMAKE_INSTALL_RPATH=$runtime_rpath")
cmake -S /workspace/source -B /workspace/build "${opts[@]}"
cmake --build /workspace/build --parallel "$BUILD_JOBS"
cmake --install /workspace/build
test -x "$INSTALL_PREFIX/bin/abacus"
LD_LIBRARY_PATH="$deps/libtorch/lib:${LD_LIBRARY_PATH:-}" "$INSTALL_PREFIX/bin/abacus" --version

# Preserve small upstream scientific inputs for subsequent host-MPI checks.
python3 - "$INSTALL_PREFIX" <<'PY'
from pathlib import Path
import re, shutil, sys
source = Path('/workspace/source')
destination = Path(sys.argv[1]) / 'share/sai/lts-cases'
cases = {
    'pw': 'tests/integrate/102_PW_CG_GPU',
    'hse': 'tests/integrate/381_NO_GO_S1_HSE',
    'deepks': 'tests/deepks/603_NO_deepks_CH4',
    'cusolvermp': 'tests/integrate/934_NO_Si2_dzp_GPU',
}
for name, relative in cases.items():
    original, case = source / relative, destination / name
    (case / 'PP_ORB').mkdir(parents=True)
    for filename in ('INPUT', 'KPT', 'STRU', 'result.ref'):
        shutil.copyfile(original / filename, case / filename)
    if (original / 'model.ptg').exists():
        shutil.copyfile(original / 'model.ptg', case / 'model.ptg')
    for filename in ('INPUT', 'STRU'):
        text = (case / filename).read_text()
        for token in re.findall(r'[A-Za-z0-9_./+-]+\.(?:upf|UPF|orb)', text):
            path = original / token if '/' in token else source / 'tests/PP_ORB' / token
            if not path.resolve().is_relative_to(source):
                raise ValueError('fixture leaves source tree')
            shutil.copyfile(path, case / 'PP_ORB' / path.name)
            if '/' in token:
                text = text.replace(token, './PP_ORB/' + path.name)
        (case / filename).write_text(text.replace('../../PP_ORB', './PP_ORB'))
shutil.copytree(destination / 'pw', Path(sys.argv[1]) / 'share/sai/smoke-case')
for package in ('libtorch', 'LibRI-b0eff7a', 'LibComm-master', 'cereal-master',
                'rapidjson-master', 'libnpy-1.0.1'):
    licenses = Path(sys.argv[1]) / 'share/licenses' / package
    licenses.mkdir(parents=True)
    for pattern in ('LICENSE*', 'COPYING*', 'NOTICE*'):
        for path in (Path('/workspace/dependencies') / package).glob(pattern):
            if path.is_file():
                shutil.copyfile(path, licenses / path.name)
PY
