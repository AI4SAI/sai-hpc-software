#!/bin/bash
# Run against real Lmod and an installed or staged native Python application.
# Arguments: module root, module name, application prefix, command, evidence directory.
set -eo pipefail
trap 'printf "FAILED at line %s: %s\n" "$LINENO" "$BASH_COMMAND" >&2' ERR
[[ $# == 5 ]]
module_root=$1
app_module=$2
app_prefix=$3
app_command=$4
evidence=$5
mkdir -p "$evidence"
source /etc/profile.d/lmod.sh
module purge
module use "$module_root"

# Inspection must work without loading a Python environment or running the application.
module --ignore_cache whatis "$app_module" > "$evidence/whatis.txt" 2>&1
module --ignore_cache help "$app_module" > "$evidence/help.txt" 2>&1
[[ $(rg -c 'Features:' "$evidence/whatis.txt") == 1 ]]
[[ -z ${SAI_PYTHON_ENV:-} ]]
while read -r _ record; do
    [[ -r "$record" ]]
done < <(rg -o 'cat /[^[:space:]]+' "$evidence/help.txt")

# A foreign Python standard library must not override the selected application.
export PYTHONPATH="$evidence/foreign-packages"
export PYTHONHOME="$evidence/foreign-python"
export PYTHONNOUSERSITE=0 PYTHONDONTWRITEBYTECODE=0
original_path=$PATH
original_library_path=${LD_LIBRARY_PATH-}
module load "$app_module"
[[ $(command -v "$app_command") == "$app_prefix/bin/$app_command" ]]
[[ $(command -v python) == "$app_prefix/bin/python" ]]
[[ $SAI_PYTHON_ENV == "$app_prefix" ]]
[[ -z ${PYTHONPATH:-} && -z ${PYTHONHOME:-} ]]
[[ $PYTHONNOUSERSITE == 1 && $PYTHONDONTWRITEBYTECODE == 1 ]]
[[ ${LD_LIBRARY_PATH-} == "$original_library_path" ]]
python -B - "$app_prefix" <<'PY'
from pathlib import Path
import site
import sys
import numpy

prefix = Path(sys.argv[1])
assert Path(sys.prefix) == prefix, (sys.prefix, prefix)
assert Path(numpy.__file__).is_relative_to(prefix), numpy.__file__
assert site.ENABLE_USER_SITE is False
assert sys.dont_write_bytecode is True
print('PYTHON_IMPORT_ISOLATION_PASSED', sys.version.split()[0])
PY

# With auto-swap disabled, the existing phonopy environment must be rejected.
if LMOD_AUTO_SWAP=no module load phonopy/2.48.0 > "$evidence/family-conflict.txt" 2>&1; then
    printf 'Two Python environments were allowed simultaneously\n' >&2
    exit 1
fi
module is-loaded "$app_module"
if module is-loaded phonopy/2.48.0; then exit 1; fi
[[ $SAI_PYTHON_ENV == "$app_prefix" ]]
module unload "$app_module"
[[ $PATH == "$original_path" ]]
[[ $PYTHONPATH == "$evidence/foreign-packages" ]]
[[ $PYTHONHOME == "$evidence/foreign-python" ]]
[[ $PYTHONNOUSERSITE == 0 && $PYTHONDONTWRITEBYTECODE == 0 ]]
[[ -z ${SAI_PYTHON_ENV:-} ]]
[[ ${LD_LIBRARY_PATH-} == "$original_library_path" ]]

# Unset settings remain unset. This Lmod normalizes saved empty values to unset,
# including for the existing Lua phonopy module; Python treats both as empty.
python_settings=(PYTHONPATH PYTHONHOME PYTHONNOUSERSITE PYTHONDONTWRITEBYTECODE)
unset "${python_settings[@]}"
module load "$app_module"
module unload "$app_module"
for setting in "${python_settings[@]}"; do [[ ! -v $setting ]]; done
export PYTHONPATH='' PYTHONHOME='' PYTHONNOUSERSITE='' PYTHONDONTWRITEBYTECODE=''
module load "$app_module"
module unload "$app_module"
for setting in "${python_settings[@]}"; do [[ -z ${!setting} ]]; done
[[ $PATH == "$original_path" ]]
printf 'PYTHON_MODULE_ENVIRONMENT_PASSED %s\n' "$app_module"
