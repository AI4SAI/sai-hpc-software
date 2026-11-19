#!/usr/bin/env bash
# Independent LTS pilot: standard image driver, with no develop publication.
set -euo pipefail
umask 022
op=$1; software=$2; sha=$3; version=$4; target=$5; delivery=${6:?identity required}
[[ "$software" == abacus && "$sha" =~ ^[0-9a-f]{40}$ && "$version" == LTS-* ]]
export PATH=/usr/bin:/bin TMPDIR=/workspace/tmp
INSTALL_PREFIX=$(python3 /control/delivery_layout.py prefix "$delivery" "$software" "$sha" "$version" "$target")
export INSTALL_PREFIX
environment() {
  source /control/environment.sh "$target"
  if [[ "$target" != dsprhbm ]]; then
    set +u
    module unload nvmplibs/26.7-tmp
    set -u
    export LTS_MATH_ROOT=/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/25.7/math_libs/12.9/targets/x86_64-linux
    test -s "$LTS_MATH_ROOT/include/cal.h"
    test -s "$LTS_MATH_ROOT/lib/libcal.so"
    test -s "$LTS_MATH_ROOT/lib/libcusolverMp.so"
    export CPATH="$LTS_MATH_ROOT/include:${CPATH:-}"
    export LD_LIBRARY_PATH="$LTS_MATH_ROOT/lib:${LD_LIBRARY_PATH:-}"
  fi
}
case "$op" in
  build)
    mkdir -p /workspace/tmp
    test ! -w /input/repository
    test ! -w /control
    test ! -w /opt/devtools
    python3 - "$sha" <<'PY'
import hashlib, json, sys, tarfile
from pathlib import Path
record = json.loads(Path('/control/abacus_dependency_lock.json').read_text())['lts_source']
assert record['source_sha'] == sys.argv[1] and record['tree_verified'] is True
archive = Path('/input/abacus-updates') / record['archive']
assert hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest() == record['archive_sha256']
with tarfile.open(archive, 'r:gz') as stream:
    stream.extractall('/workspace/lts-source', filter='data')
roots = list(Path('/workspace/lts-source').iterdir())
assert len(roots) == 1 and roots[0].is_dir() and not roots[0].is_symlink()
roots[0].rename('/workspace/source')
PY
    environment
    bash /control/abacus_build.sh "$target"
    metadata="$INSTALL_PREFIX/share/sai"
    printf '%s\n' "$delivery" > "$metadata/release-identity.json"
    printf '%s\n' "$sha" > "$metadata/source-sha"
    printf '%s\n' "$target" > "$metadata/target"
    module -t list > "$metadata/modules.txt" 2>&1
    lscpu > "$metadata/hardware.txt"
    cp /workspace/build/CMakeCache.txt "$metadata/"
    cp /workspace/dependencies/dependency-lock.json "$metadata/"
    {
      printf '%s\n' '# Source inside the SIF or after installation at the recorded prefix.'
      for key in PATH LD_LIBRARY_PATH OPAL_PREFIX PMIX_INSTALL_PREFIX MPI_HOME OMPI_HOME; do
        if [[ -v "$key" ]]; then printf 'export %s=%q\n' "$key" "${!key}"; fi
      done
      printf 'export LD_LIBRARY_PATH=%q:"${LD_LIBRARY_PATH:-}"\n' "$INSTALL_PREFIX/dependencies/libtorch/lib"
      printf 'export PATH=%q:"$PATH"\n' "$INSTALL_PREFIX/bin"
    } > "$metadata/runtime-env.sh"
    ;;
  export)
    test ! -e /workspace/export
    bash /control/create_rootfs.sh /workspace/export
    mkdir -p "/workspace/export$(dirname -- "$INSTALL_PREFIX")"
    cp -a "$INSTALL_PREFIX" "/workspace/export$INSTALL_PREFIX"
    chmod -R a+rX,u+w,go-w "/workspace/export$INSTALL_PREFIX"
    mksquashfs /workspace/export /workspace/final.squashfs -noappend -all-root -no-xattrs -processors "$BUILD_JOBS"
    ;;
  verify)
    python3 /control/delivery_layout.py prefix "$delivery" "$software" "$sha" "$version" "$target" \
      --installed "$INSTALL_PREFIX/share/sai/release-identity.json"
    source "$INSTALL_PREFIX/share/sai/runtime-env.sh"
    test "$(cat "$INSTALL_PREFIX/share/sai/source-sha")" = "$sha"
    "$INSTALL_PREFIX/bin/abacus" --version
    dependencies=$(ldd "$INSTALL_PREFIX/bin/abacus")
    printf '%s\n' "$dependencies"
    [[ "$dependencies" != *"not found"* ]]
    for option in ENABLE_MPI USE_OPENMP ENABLE_LCAO USE_ELPA ENABLE_LIBXC ENABLE_LIBRI ENABLE_LIBCOMM ENABLE_DEEPKS ENABLE_RAPIDJSON; do
      grep -qx "$option:BOOL=ON" "$INSTALL_PREFIX/share/sai/CMakeCache.txt"
    done
    for library in libmpi libelpa libxc libtorch_cpu; do
      grep -q "$library.*=> /" <<< "$dependencies"
    done
    if [[ "$target" != dsprhbm ]]; then
      grep -qx 'USE_CUDA:BOOL=ON' "$INSTALL_PREFIX/share/sai/CMakeCache.txt"
      for library in libcal libcusolverMp; do
        grep -q "$library.*=> /opt/devtools/nvidia/hpc_sdk/Linux_x86_64/25.7/math_libs/12.9/" <<< "$dependencies"
      done
    else
      grep -qx 'USE_CUDA:BOOL=OFF' "$INSTALL_PREFIX/share/sai/CMakeCache.txt"
    fi
    if touch "$INSTALL_PREFIX/.write-test" 2>/dev/null; then exit 1; fi
    ;;
  *) exit 2 ;;
esac
case "$op" in build|export) python3 /control/cp2k_overlay_sync.py "$op" ;; esac
