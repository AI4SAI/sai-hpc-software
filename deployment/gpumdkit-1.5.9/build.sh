#!/bin/bash
# Executed inside the minimal SIF with an empty project-backed prefix bound here.
set -euo pipefail
prefix=/opt/apps/GPUMDkit/1.5.9
[[ -d "$prefix" && ! -e "$prefix/bin/python" ]]
export TMPDIR=/runtime/tmp TMP=/runtime/tmp TEMP=/runtime/tmp
export CONDA_PKGS_DIRS=/runtime/conda-pkgs PIP_CACHE_DIR=/runtime/pip-cache
export CONDARC=/dev/null PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export CFLAGS='-O2 -march=x86-64 -mtune=generic'
export CXXFLAGS='-O2 -march=x86-64 -mtune=generic'
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
mkdir -p "$TMPDIR"
/opt/devtools/anaconda3/bin/conda create --offline --copy --yes --prefix "$prefix" \
    --file /input/python-explicit.txt
export PATH="$prefix/bin:/opt/devtools/gcc/14.3.0/bin:/usr/bin:/bin"
export CC=/opt/devtools/gcc/14.3.0/bin/gcc CXX=/opt/devtools/gcc/14.3.0/bin/g++
export LD_LIBRARY_PATH="$prefix/lib:/opt/devtools/gcc/14.3.0/lib64"
"$prefix/bin/python" -m pip install --no-index --find-links=/input/wheels pybind11 setuptools-scm numpy
"$prefix/bin/python" -m pip install --no-index --find-links=/input/wheels --no-build-isolation \
    -r /control/requirements.in
tar -xzf /input/source.tar.gz -C /runtime
cd /runtime/GPUMDkit-1.5.9
PREFIX="$prefix" bash conda/build.sh
mkdir -p "$prefix/share/licenses/gpumdkit"
cp LICENCE "$prefix/share/licenses/gpumdkit/LICENCE"
"$prefix/bin/python" -m pip check
"$prefix/bin/python" -m pip freeze > /runtime/requirements.lock
"$prefix/bin/gpumdkit" -h > /runtime/help.txt
"$prefix/bin/gpumdkit" -doctor > /runtime/doctor.txt
find "$prefix" -type d -name __pycache__ -prune -exec rm -rf {} +
printf 'GPUMDKIT_CONTAINER_INSTALL_PASSED\n'
