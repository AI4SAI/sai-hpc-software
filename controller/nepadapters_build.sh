#!/usr/bin/env bash
# Run only inside the new writable overlay; the original LAMMPS overlay is read-only.
set -euo pipefail
target=$1
phase=$2
source /control/md_environment.sh "$target"
eval "$(/usr/bin/python3 - <<'PY'
import json, shlex
d = json.load(open('/input/request.json'))
for key, value in {'NEPADAPTERS_PREFIX': d['prefix'], 'LAMMPS_PREFIX': d['lammps']['prefix'],
                   'DEEPMD_PREFIX': d['deepmd']['prefix']}.items():
    print('export ' + key + '=' + shlex.quote(value))
PY
)"
export PATH="$LAMMPS_PREFIX/bin:$PATH"
export LD_LIBRARY_PATH="$LAMMPS_PREFIX/lib:$DEEPMD_PREFIX/lib:$MD_SYSTEM_DEEPMD/lib/python3.13/site-packages/torch/lib:$MD_SYSTEM_DEEPMD/lib/python3.13/site-packages/tensorflow:$MD_SYSTEM_DEEPMD/lib:$LD_LIBRARY_PATH"
export OMP_NUM_THREADS=1
if [[ "$phase" == build ]]; then export TMPDIR=/workspace/nepadapters-tmp; else export TMPDIR=/runtime; fi
mkdir -p "$TMPDIR"
if [[ "$phase" == build ]]; then
    [[ "$(git -C /workspace/lammps rev-parse HEAD)" == 8de817dd79bfe4525d5d39246a212d833e6dee07 ]]
    /usr/bin/python3 - <<'PY'
import os
cpus = sorted(os.sched_getaffinity(0))
assert len(cpus) >= int(os.environ['BUILD_JOBS']), cpus
print('NEPADAPTERS_BUILD_AFFINITY', len(cpus), cpus, flush=True)
PY
    mkdir /workspace/nepadapters
    tar -xzf /input/nepadapters.tar.gz -C /workspace/nepadapters
    /usr/bin/python3 /control/nepadapters_compat.py /workspace/nepadapters /workspace/lammps /runtime/compatibility.json
    cmake -S /workspace/nepadapters -B /workspace/nepadapters-build \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$NEPADAPTERS_PREFIX" \
        -DCMAKE_INSTALL_LIBDIR=lib -DBUILD_SHARED_LIBS=OFF \
        -DCMAKE_SKIP_INSTALL_RPATH=ON -DNEP_ADAPTERS_CPU_ENABLE_NATIVE_ARCH=OFF \
        -DCMAKE_CXX_COMPILER="$CXX" -DCMAKE_CUDA_HOST_COMPILER="$CXX" \
        -DCMAKE_CXX_FLAGS=-fopenmp -DCMAKE_CUDA_FLAGS=-Xcompiler=-fopenmp \
        -DCMAKE_CXX_STANDARD=20 -DCMAKE_CUDA_STANDARD=20 -DCMAKE_CUDA_ARCHITECTURES=70 \
        -DNEP_ADAPTERS_ENABLE_CPU=ON -DNEP_ADAPTERS_ENABLE_CUDA=ON \
        -DNEP_ADAPTERS_ENABLE_PYTHON=OFF -DNEP_ADAPTERS_ENABLE_LAMMPS=ON \
        -DNEP_ADAPTERS_BUILD_TESTS=ON -DNEP_ADAPTERS_INSTALL_DEVELOPMENT_FILES=OFF \
        -DNEP_ADAPTERS_LAMMPS_SOURCE_DIR=/workspace/lammps \
        -DNEP_ADAPTERS_LAMMPS_KOKKOS_BUILD_DIR=/workspace/lammps-build
    cmake --build /workspace/nepadapters-build --parallel "$BUILD_JOBS" \
        --target nepadapterscpuplugin nepadaptersgpuplugin nep_adapters_cpu_spin_test
    ctest --test-dir /workspace/nepadapters-build --output-on-failure --no-tests=error \
        -R '^nep_adapters_cpu_spin(_openmp)?_test$' \
        --output-junit /runtime/upstream-spin-tests.xml
    cmake --install /workspace/nepadapters-build
    # MPI is supplied by the loaded LAMMPS module on every supported partition.
    for backend in cpu gpu; do
        # CMake can retain the MPI wrapper's toolchain RPATH even when its own
        # install RPATH is disabled. Remove that final ELF search path as well.
        chrpath -d "$NEPADAPTERS_PREFIX/lib/nepadapters${backend}plugin.so"
        if readelf -d "$NEPADAPTERS_PREFIX/lib/nepadapters${backend}plugin.so" | grep -Eq '\((RPATH|RUNPATH)\)'; then
            echo 'Unexpected embedded runtime library path' >&2
            exit 1
        fi
    done
    mkdir -p "$NEPADAPTERS_PREFIX/share/licenses/nepadapters"
    cp /workspace/nepadapters/engines/cpu/native/LICENSE.NEP_CPU "$NEPADAPTERS_PREFIX/share/licenses/nepadapters/"
    cp /workspace/nepadapters-build/CMakeCache.txt /runtime/CMakeCache.txt
    export LAMMPS_PLUGIN_PATH="$NEPADAPTERS_PREFIX/lib"
    "$LAMMPS_PREFIX/bin/lmp" -h > /runtime/lammps-help.txt
    for backend in cpu gpu; do
        ldd "$NEPADAPTERS_PREFIX/lib/nepadapters${backend}plugin.so" > "/runtime/ldd-${backend}.txt"
        ! grep -q 'not found' "/runtime/ldd-${backend}.txt"
    done
    /usr/bin/python3 /control/nepadapters_science.py prepare /workspace/nepadapters /runtime/cases
    /usr/bin/python3 /control/nepadapters_science.py run /runtime/cases "$LAMMPS_PREFIX/bin/lmp" "$NEPADAPTERS_PREFIX/lib" /runtime/science-build
    bash /control/create_rootfs.sh /workspace/nepadapters-export
    mkdir -p "/workspace/nepadapters-export$(dirname "$NEPADAPTERS_PREFIX")"
    cp -a "$NEPADAPTERS_PREFIX" "/workspace/nepadapters-export$NEPADAPTERS_PREFIX"
    env -u LD_PRELOAD mksquashfs /workspace/nepadapters-export /workspace/nepadapters-final.squashfs \
        -noappend -all-root -no-xattrs -processors "$BUILD_JOBS"
elif [[ "$phase" == verify ]]; then
    export TMPDIR=/runtime
    /usr/bin/python3 /control/nepadapters_science.py run /runtime/cases "$LAMMPS_PREFIX/bin/lmp" "$NEPADAPTERS_PREFIX/lib" /runtime/science-sif
else
    exit 2
fi
if [[ "$phase" == build ]]; then
    /usr/bin/python3 - <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, '/control')
from cp2k_overlay_sync import synchronize
synchronize('build', results=Path('/runtime'))
PY
fi
echo "NEPADAPTERS_${phase^^}_PASSED"
