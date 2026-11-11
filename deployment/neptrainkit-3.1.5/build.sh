#!/bin/bash
# Executed inside Apptainer; only the new application prefix and job scratch are writable.
set -euo pipefail
prefix=/opt/apps/neptrainkit/3.1.5
export PATH=/opt/devtools/gcc/14.3.0/bin:/opt/devtools/anaconda3/bin:/usr/bin:/bin
export LD_LIBRARY_PATH=/opt/devtools/gcc/14.3.0/lib64:/opt/devtools/gcc/14.3.0/lib
export TMPDIR=/workspace/tmp CONDA_PKGS_DIRS=/workspace/conda-pkgs
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
export HOME=/runtime/home XDG_CACHE_HOME=/runtime/cache XDG_CONFIG_HOME=/runtime/config
mkdir -p "$TMPDIR" "$CONDA_PKGS_DIRS" "$HOME" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME"
conda create --offline --yes --no-deps --copy --prefix "$prefix" --file /input/seed/python-explicit.txt
"$prefix/bin/python" -m venv --copies /workspace/build-env
/workspace/build-env/bin/python -m pip install --no-index --no-compile --find-links=/input/wheels \
    setuptools wheel numpy pybind11 setuptools-scm pytest pytest-qt dpdata
mkdir /workspace/source
tar -xzf /input/source.tar.gz --strip-components=1 -C /workspace/source
export CC=/opt/devtools/gcc/14.3.0/bin/gcc CXX=/opt/devtools/gcc/14.3.0/bin/g++
export CFLAGS='-O3 -march=x86-64 -mtune=generic'
export CXXFLAGS="$CFLAGS"
export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_NEPTRAINKIT=3.1.5 NEPKIT_OPENMP=1
/workspace/build-env/bin/python -m pip wheel --no-index --no-deps --no-build-isolation \
    --wheel-dir=/runtime/built-wheels /workspace/source
"$prefix/bin/python" -m pip install --no-index --no-compile --find-links=/input/wheels \
    /runtime/built-wheels/neptrainkit-3.1.5-*.whl
"$prefix/bin/python" -m pip check
"$prefix/bin/python" -m pip freeze > /runtime/requirements.txt
"$prefix/bin/python" -m pip list --format=json > /runtime/packages.json
"$prefix/bin/nepkit" --neptrainkit-native-health-check
# The tests are copied without src/, so imports necessarily use the installed wheel.
cp -a /workspace/source/tests /runtime/tests
export QT_QPA_PLATFORM=offscreen LOCALAPPDATA=/runtime/localappdata
export XDG_RUNTIME_DIR=/runtime/xdg NEPTRAINKIT_RUNTIME_ROOT=/runtime/managed-nep
mkdir -p "$LOCALAPPDATA" "$XDG_RUNTIME_DIR" "$NEPTRAINKIT_RUNTIME_ROOT"
chmod 700 "$XDG_RUNTIME_DIR"
export PYTHONPATH=/workspace/build-env/lib/python3.12/site-packages
"$prefix/bin/python" -m pytest -q --import-mode=importlib \
    /runtime/tests/test_calculator.py /runtime/tests/test_structure.py \
    --junitxml=/runtime/upstream-tests.xml
unset PYTHONPATH
"$prefix/bin/python" -B /control/science.py /runtime/tests/data/nep /runtime/build-science.json
"$prefix/bin/python" -B /control/gui-smoke.py /runtime/gui-smoke.json
chmod -R a+rX "$prefix"
echo NEPTRAINKIT_BUILD_ACCEPTED
