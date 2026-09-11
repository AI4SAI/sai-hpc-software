#!/usr/bin/env bash
# Executed only inside the file-backed overlay on the native target partition.
set -euo pipefail
target=$1
source /control/md_environment.sh "$target"
[[ "$DEEPMD_PREFIX" == /opt/software/deepmd-kit/* && "$LAMMPS_PREFIX" == /opt/software/lammps/* ]]
mkdir -p "$DEEPMD_PREFIX/share/sai" "$LAMMPS_PREFIX/share/sai"
site=$MD_SYSTEM_DEEPMD/lib/python3.13/site-packages
# The site's old DeepMD backend RUNPATH contains a stale .new prefix. Resolve
# backend dependencies from the current immutable system environment explicitly.
export LD_LIBRARY_PATH="$site/tensorflow:$site/torch/lib:$MD_SYSTEM_DEEPMD/lib:${LD_LIBRARY_PATH:-}"
"$MD_SYSTEM_DEEPMD/bin/python" /control/md_probe.py --deepmd "$MD_SYSTEM_DEEPMD" \
  --lammps "$MD_SYSTEM_LAMMPS" > /workspace/baseline.json
cp /workspace/baseline.json "$LAMMPS_PREFIX/share/sai/baseline.json"
module -t list > "$LAMMPS_PREFIX/share/sai/modules.txt" 2>&1
lscpu > "$LAMMPS_PREFIX/share/sai/lscpu.txt"
gcc -march=native -Q --help=target > "$LAMMPS_PREFIX/share/sai/native-flags.txt"
nvcc --version > "$LAMMPS_PREFIX/share/sai/cuda.txt"
"$MD_SYSTEM_DEEPMD/bin/python" - <<'PY'
import importlib.metadata as m
import tensorflow as tf
import torch
assert tf.sysconfig.CXX11_ABI_FLAG == int(torch.compiled_with_cxx11_abi()) == 1
assert torch.version.cuda and torch.version.cuda.split('.')[0] == '12'
assert 'sm_70' in torch.cuda.get_arch_list(), 'site PyTorch dropped Volta; build a compatible dependency first'
assert torch.cuda.is_available(), 'GPU acceptance requires an allocated visible GPU'
for dependency in ('scikit-build-core', 'packaging', 'dependency_groups'):
    print(dependency, m.version(dependency))
PY
# System site-packages are read-only dependencies; only this new package is
# installed into its final canonical prefix, never a temporary staging prefix.
"$MD_SYSTEM_DEEPMD/bin/python" -m venv --system-site-packages "$DEEPMD_PREFIX"
export DP_VARIANT=cuda DP_ENABLE_TENSORFLOW=1 DP_ENABLE_PYTORCH=1 DP_ENABLE_IPI=1
export DP_ENABLE_NATIVE_OPTIMIZATION=1 CMAKE_BUILD_PARALLEL_LEVEL="$BUILD_JOBS"
export CMAKE_ARGS="-DCMAKE_CUDA_ARCHITECTURES=70 -DCMAKE_INSTALL_RPATH=\$ORIGIN -DENABLE_JAX=ON"
"$DEEPMD_PREFIX/bin/python" -m pip install --no-index --no-deps --no-build-isolation \
  --ignore-installed /workspace/deepmd-kit
cmake -S /workspace/deepmd-kit/source -B /workspace/deepmd-cpp \
  -DCMAKE_INSTALL_PREFIX="$DEEPMD_PREFIX" -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_RPATH='$ORIGIN' -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=OFF \
  -DCMAKE_CXX_FLAGS='-march=native -mtune=native' -DCMAKE_CUDA_ARCHITECTURES=70 \
  -DENABLE_NATIVE_OPTIMIZATION=ON -DUSE_CUDA_TOOLKIT=ON \
  -DENABLE_TENSORFLOW=ON -DENABLE_PYTORCH=ON -DENABLE_JAX=ON \
  -DUSE_TF_PYTHON_LIBS=ON -DUSE_PT_PYTHON_LIBS=ON \
  -DPython_EXECUTABLE="$DEEPMD_PREFIX/bin/python" -DBUILD_CPP_IF=ON -DBUILD_PY_IF=OFF
cmake --build /workspace/deepmd-cpp -j "$BUILD_JOBS"
cmake --install /workspace/deepmd-cpp
# Official built-in integration, from the SAME source revision as the new C API.
printf '\ninclude(/workspace/deepmd-kit/source/lmp/builtin.cmake)\n' >> /workspace/lammps/cmake/CMakeLists.txt
mapfile -t packages < <("$MD_SYSTEM_DEEPMD/bin/python" - <<'PY'
import json, re
for package in json.load(open('/workspace/baseline.json'))['lammps']['packages']:
    if not re.fullmatch(r'[A-Z0-9_-]+', package):
        raise ValueError('invalid baseline package')
    print('-DPKG_' + package + '=ON')
PY
)
cmake -S /workspace/lammps/cmake -B /workspace/lammps-build \
  -DCMAKE_INSTALL_PREFIX="$LAMMPS_PREFIX" -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_RPATH='$ORIGIN;$ORIGIN/../lib' -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=OFF \
  -DCMAKE_PREFIX_PATH="$DEEPMD_PREFIX;$MD_SYSTEM_PLUMED;$MD_SYSTEM_DEEPMD" \
  -DCMAKE_CXX_FLAGS='-march=native -mtune=native' \
  -DBUILD_SHARED_LIBS=ON -DBUILD_MPI=ON -DBUILD_OMP=ON \
  -DPKG_KOKKOS=ON -DKokkos_ENABLE_CUDA=ON -DKokkos_ENABLE_OPENMP=ON \
  -DKokkos_ARCH_VOLTA70=ON -DCMAKE_CXX_COMPILER=/workspace/lammps/lib/kokkos/bin/nvcc_wrapper \
  -DPKG_GPU=ON -DGPU_API=cuda -DGPU_ARCH=sm_70 \
  -DPKG_PLUMED=ON -DPLUMED_MODE=runtime -DDOWNLOAD_PLUMED=OFF \
  -DPLUMED_INCLUDE_DIR="$MD_SYSTEM_PLUMED/include" \
  -DPKG_PYTHON=ON -DPython_EXECUTABLE="$DEEPMD_PREFIX/bin/python" "${packages[@]}"
cmake --build /workspace/lammps-build -j "$BUILD_JOBS"
cmake --install /workspace/lammps-build
cp /workspace/deepmd-cpp/CMakeCache.txt "$DEEPMD_PREFIX/share/sai/"
cp /workspace/lammps-build/CMakeCache.txt "$LAMMPS_PREFIX/share/sai/"
# Runtime needs the new C/C++ implementation and backend operators, not the
# installed old deepmd package. The backend framework libraries remain shared.
export PATH="$LAMMPS_PREFIX/bin:$DEEPMD_PREFIX/bin:$PATH"
export LD_LIBRARY_PATH="$LAMMPS_PREFIX/lib:$DEEPMD_PREFIX/lib:$DEEPMD_PREFIX/lib/python3.13/site-packages/deepmd/lib:$LD_LIBRARY_PATH"
export LAMMPS_POTENTIALS="$LAMMPS_PREFIX/share/lammps/potentials"
export PYTHONPATH="$DEEPMD_PREFIX/lib/python3.13/site-packages:${PYTHONPATH:-}"
for name in PATH LD_LIBRARY_PATH PYTHONPATH PLUMED_KERNEL LAMMPS_POTENTIALS; do
  printf 'export %s=%q\n' "$name" "${!name}"
done > "$LAMMPS_PREFIX/share/sai/runtime-env.sh"
"$DEEPMD_PREFIX/bin/python" /control/md_probe.py --deepmd "$DEEPMD_PREFIX" \
  --lammps "$LAMMPS_PREFIX" > "$LAMMPS_PREFIX/share/sai/candidate.json"
"$DEEPMD_PREFIX/bin/python" - <<'PY'
import json, os, sys
sys.path.insert(0, '/control')
from md_evidence import verify_parity
p = os.environ['LAMMPS_PREFIX'] + '/share/sai/'
print(verify_parity(json.load(open(p+'baseline.json')), json.load(open(p+'candidate.json'))))
PY
# Scientific fixtures remain inside SIF; acceptance copies only task data out.
mkdir -p "$LAMMPS_PREFIX/share/sai/upstream-tests"
cp -a /workspace/deepmd-kit/source/lmp/tests "$LAMMPS_PREFIX/share/sai/upstream-tests/lmp"
cp -a /workspace/deepmd-kit/source/tests/infer "$LAMMPS_PREFIX/share/sai/upstream-tests/infer"
echo MD_NATIVE_BUILD_FEATURE_PARITY_PASSED
