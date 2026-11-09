#!/usr/bin/env bash
# Experiment-only wrapper. The production recipe and feature gates still run.
set -euo pipefail
case "${SAI_BENCH_GENERATOR:?}" in 'Unix Makefiles'|Ninja) ;; *) exit 2 ;; esac
if [[ "$1" == build ]]; then
  cmake() {
    local phase
    local -a extra=()
    case "$1" in
      -S)
        phase=configure
        extra=(-G "$SAI_BENCH_GENERATOR")
        if [[ "$SAI_BENCH_GENERATOR" == Ninja ]]; then
          extra+=(-DCMAKE_MAKE_PROGRAM=/control/benchmark/ninja)
        fi
        ;;
      --build) phase=build ;;
      --install) phase=install ;;
      *) /opt/devtools/cmake/3.31.6/bin/cmake "$@"; return ;;
    esac
    /usr/bin/python3 /control/benchmark/measure.py "/runtime/cmake-$phase.json" \
      /opt/devtools/cmake/3.31.6/bin/cmake "$@" "${extra[@]}"
  }
  export -f cmake
fi
bash /control/md_lammps_standalone_entry.sh "$@"
if [[ "$1" == build ]]; then
  cp /workspace/lammps-build/CMakeCache.txt /runtime/
  cp /workspace/lammps-build/compile_commands.json /runtime/
  if [[ -f /workspace/lammps-build/.ninja_log ]]; then
    cp /workspace/lammps-build/.ninja_log /runtime/ninja-log.tsv
  fi
  /usr/bin/python3 - "$2" <<'PY'
import json, pathlib, shutil, sys
prefix = pathlib.Path(json.loads(sys.argv[1])['identity']['install_prefix'])
out = pathlib.Path('/runtime/build-metadata')
out.mkdir()
for source in (prefix / 'share/sai').glob('*.json'):
    shutil.copyfile(source, out / source.name)
build = pathlib.Path('/workspace/lammps-build')
for source in build.rglob('*.rsp'):
    target = pathlib.Path('/runtime/cmake-response-files') / source.relative_to(build)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
PY
fi
