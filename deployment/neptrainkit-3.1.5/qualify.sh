#!/bin/bash
set -euo pipefail
prefix=/opt/apps/neptrainkit/3.1.5
export TMPDIR=/runtime/tmp HOME=/runtime/home XDG_CACHE_HOME=/runtime/cache XDG_CONFIG_HOME=/runtime/config
export QT_QPA_PLATFORM=offscreen LOCALAPPDATA=/runtime/localappdata
export XDG_RUNTIME_DIR=/runtime/xdg NEPTRAINKIT_RUNTIME_ROOT=/runtime/managed-nep
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
mkdir -p "$TMPDIR" "$HOME" "$XDG_CACHE_HOME" "$XDG_CONFIG_HOME" "$XDG_RUNTIME_DIR" "$LOCALAPPDATA" "$NEPTRAINKIT_RUNTIME_ROOT"
chmod 700 "$XDG_RUNTIME_DIR"
"$prefix/bin/python" -m venv --copies /runtime/testenv
/runtime/testenv/bin/python -m pip install --no-index --no-compile --find-links=/input/wheels pytest pytest-qt dpdata
export PYTHONPATH=/runtime/testenv/lib/python3.12/site-packages
"$prefix/bin/python" -m pytest -q --import-mode=importlib \
    /runtime/tests/test_calculator.py /runtime/tests/test_structure.py \
    --junitxml=/runtime/upstream-tests.xml
unset PYTHONPATH
"$prefix/bin/nepkit" --neptrainkit-native-health-check
"$prefix/bin/python" -B /control/science.py /runtime/tests/data/nep /runtime/build-science.json
"$prefix/bin/python" -B /control/gui-smoke.py /runtime/gui-smoke.json
echo NEPTRAINKIT_QUALIFICATION_PASSED
