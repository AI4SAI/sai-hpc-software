#!/usr/bin/env bash
# Build/install only inside the overlay; science executes the completed SIF.
set -euo pipefail
phase=$1
export SAI_DEEPMD_DELIVERY=$2
settings=$(/usr/bin/python3 - <<'PY'
import json, os, sys
sys.path.insert(0, '/control')
from deepmd_standalone import validate_delivery
identity = validate_delivery(json.loads(os.environ['SAI_DEEPMD_DELIVERY']))['identity']
for key in ('target', 'source_sha', 'install_prefix'):
    print(identity[key])
PY
)
mapfile -t settings <<< "$settings"
target=${settings[0]}; sha=${settings[1]}
export DEEPMD_PREFIX=${settings[2]}
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
case "$phase" in
  build)
    export TMPDIR=/workspace/tmp
    mkdir -p "$TMPDIR"
    /usr/bin/python3 - <<'PY'
import os
cpus = sorted(os.sched_getaffinity(0))
if len(cpus) < int(os.environ['BUILD_JOBS']):
    raise ValueError('container cannot supply the requested compile workers')
print('DEEPMD_BUILD_AFFINITY', len(cpus), cpus, flush=True)
PY
    git init /workspace/deepmd-kit
    git -C /workspace/deepmd-kit -c core.hooksPath=/dev/null fetch --depth=1 --no-tags /input/deepmd-kit "$sha"
    git -C /workspace/deepmd-kit -c core.hooksPath=/dev/null checkout --detach "$sha"
    [[ "$(git -C /workspace/deepmd-kit rev-parse HEAD)" == "$sha" ]]
    /usr/bin/python3 - <<'PY'
import json, os, sys
sys.path.insert(0, '/control')
from md_source_version import restore_release_tag
identity = json.loads(os.environ['SAI_DEEPMD_DELIVERY'])['identity']
print(restore_release_tag('/workspace/deepmd-kit', dict(sha=identity['source_sha'],
      track=identity['track'], ref=identity['source_ref'])))
PY
    source /control/md_environment.sh "$target"
    export LD_LIBRARY_PATH="$("$MD_SYSTEM_DEEPMD/bin/python" /control/md_driver_paths.py)"
    site=$MD_SYSTEM_DEEPMD/lib/python3.13/site-packages
    export LD_LIBRARY_PATH="$site/tensorflow:$site/torch/lib:$MD_SYSTEM_DEEPMD/lib:$LD_LIBRARY_PATH"
    "$MD_SYSTEM_DEEPMD/bin/python" /control/deepmd_standalone_check.py inventory "$MD_SYSTEM_DEEPMD" > /runtime/baseline-inventory.json
    "$MD_SYSTEM_DEEPMD/bin/python" - <<'PY'
import tensorflow as tf
import torch
assert tf.sysconfig.CXX11_ABI_FLAG == int(torch.compiled_with_cxx11_abi()) == 1
assert torch.version.cuda and torch.version.cuda.split('.')[0] == '12'
assert 'sm_70' in torch.cuda.get_arch_list()
assert torch.cuda.is_available()
PY
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
    cp /workspace/deepmd-cpp/CMakeCache.txt /runtime/CMakeCache.txt
    export PATH="$DEEPMD_PREFIX/bin:$PATH"
    export LD_LIBRARY_PATH="$DEEPMD_PREFIX/lib:$DEEPMD_PREFIX/lib/python3.13/site-packages/deepmd/lib:$LD_LIBRARY_PATH"
    export PYTHONPATH="$DEEPMD_PREFIX/lib/python3.13/site-packages"
    # Remove application modules used to discover the site's shared frameworks.
    # The new package's runtime has no dependency on an MD engine executable.
    for name in PATH LD_LIBRARY_PATH; do
      IFS=: read -ra components <<< "${!name}"
      cleaned=()
      for component in "${components[@]}"; do
        [[ -n "$component" ]] || continue
        [[ "$component" != /opt/apps/lammps && "$component" != /opt/apps/lammps/* ]] || continue
        if [[ "$name" == PATH ]]; then
          case "$component" in /bin) component=/usr/bin ;; /sbin) component=/usr/sbin ;; esac
        fi
        cleaned+=("$component")
      done
      printf -v "$name" '%s' "$(IFS=:; printf '%s' "${cleaned[*]}")"
      export "$name"
    done
    for name in PATH LD_LIBRARY_PATH PYTHONPATH; do
      printf 'export %s=%q\n' "$name" "${!name}"
    done > /runtime/runtime-env.sh
    "$DEEPMD_PREFIX/bin/python" /control/deepmd_standalone_check.py inventory "$DEEPMD_PREFIX" > /runtime/candidate-inventory.json
    "$DEEPMD_PREFIX/bin/python" /control/md_science.py prepare /workspace/deepmd-kit /runtime/case
    chmod -R a+rX,go-w "$DEEPMD_PREFIX"
    "$DEEPMD_PREFIX/bin/python" /control/deepmd_standalone_check.py manifest
    ;;
  export)
    export TMPDIR=/workspace/tmp
    bash /control/create_rootfs.sh /workspace/export
    mkdir -p "/workspace/export$(dirname -- "$DEEPMD_PREFIX")"
    cp -a "$DEEPMD_PREFIX" "/workspace/export$DEEPMD_PREFIX"
    env -u LD_PRELOAD /usr/bin/python3 - <<'PY'
import json, os, sys
sys.path.insert(0, '/control')
from md_packaging import prepare
identity = json.loads(os.environ['SAI_DEEPMD_DELIVERY'])['identity']
print(prepare('/workspace/export', [identity]))
PY
    env -u LD_PRELOAD mksquashfs /workspace/export /workspace/final.squashfs \
      -noappend -all-root -no-xattrs -processors "$BUILD_JOBS"
    ;;
  verify|science)
    export TMPDIR=/runtime
    source /runtime/runtime-env.sh
    export OMP_NUM_THREADS=1 DP_INTRA_OP_PARALLELISM_THREADS=1 DP_INTER_OP_PARALLELISM_THREADS=1
    if [[ "$phase" == verify ]]; then
      "$DEEPMD_PREFIX/bin/dp" --version
      "$DEEPMD_PREFIX/bin/python" /control/deepmd_standalone_check.py verify
    else
      "$DEEPMD_PREFIX/bin/python" /control/deepmd_standalone_check.py science
    fi
    ;;
  *) exit 2 ;;
esac
