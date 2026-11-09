#!/usr/bin/env bash
# Experiment wrapper; actual builds use the unchanged standalone recipe.
set -euo pipefail
[[ ${SAI_BENCH_GENERATOR:?} == 'Unix Makefiles' ]]
case ${SAI_BENCH_VARIANT:?} in base|pr5308) ;; *) exit 2 ;; esac
if [[ $1 == build ]]; then
  cmake() {
    local phase code
    case "$1" in
      -S) phase=configure ;;
      --build) phase=build ;;
      --install) phase=install ;;
      *) /opt/devtools/cmake/3.31.6/bin/cmake "$@"; return ;;
    esac
    if [[ $phase == configure ]]; then
      /usr/bin/python3 /control/benchmark/provenance.py source
      set -- "$@" -G 'Unix Makefiles'
    fi
    if /usr/bin/python3 /control/benchmark/measure.py "/runtime/cmake-$phase.json" \
      /opt/devtools/cmake/3.31.6/bin/cmake "$@"; then
      code=0
    else
      code=$?
    fi
    if [[ $phase == configure || $phase == build ]]; then
      /usr/bin/python3 /control/benchmark/provenance.py collect
    fi
    return "$code"
  }
  export -f cmake
fi
bash /control/md_lammps_standalone_entry.sh "$@"
if [[ $1 == build ]]; then
  /usr/bin/python3 /control/benchmark/provenance.py collect
  /usr/bin/python3 - "$2" <<'PY'
import json, pathlib, shutil, sys
prefix = pathlib.Path(json.loads(sys.argv[1])['identity']['install_prefix'])
out = pathlib.Path('/runtime/build-metadata')
out.mkdir()
for source in (prefix / 'share/sai').glob('*.json'):
    shutil.copyfile(source, out / source.name)
PY
fi
