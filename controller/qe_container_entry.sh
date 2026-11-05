#!/usr/bin/env bash
set -euo pipefail
umask 022
op=$1; software=$2; sha=$3; version=$4; target=$5; delivery=${6:?canonical identity required}
[[ "$software" == qe && "$sha" =~ ^[0-9a-f]{40}$ ]]
export PATH=/usr/bin:/bin TMPDIR=/workspace/tmp
INSTALL_PREFIX=$(python3 /control/delivery_layout.py prefix "$delivery" "$software" "$sha" "$version" "$target")
export INSTALL_PREFIX
case "$op" in
build)
  mkdir -p /workspace/tmp
  test ! -w /control
  test ! -w /input/repository
  git init /workspace/source
  git -C /workspace/source -c core.hooksPath=/dev/null fetch --depth=1 --no-tags /input/repository "$sha"
  git -C /workspace/source -c core.hooksPath=/dev/null checkout --detach "$sha"
  python3 /control/qe_dependencies.py populate /workspace/source /input/qe-dependencies
  source /control/qe_environment.sh "$target"
  if [[ "$target" == dsprhbm ]]; then source /control/qe_hdf5.sh; fi
  bash /control/qe_build.sh
  printf '%s\n' "$sha" > "$INSTALL_PREFIX/share/sai/source-sha"
  module -t list > "$INSTALL_PREFIX/share/sai/modules.txt" 2>&1
  nvfortran --version > "$INSTALL_PREFIX/share/sai/compiler.txt" 2>&1
  mpif90 --showme > "$INSTALL_PREFIX/share/sai/mpi-wrapper.txt"
  lscpu > "$INSTALL_PREFIX/share/sai/hardware.txt"
  cp /input/qe-dependencies/qe-submodules.json "$INSTALL_PREFIX/share/sai/submodules.json"
  for key in PATH LD_LIBRARY_PATH MPI_HOME OPAL_PREFIX CUDA_HOME NVHPC_CUDA_HOME NVCOMPILER_CUDA_HOME NVCOMPILER_MATH_LIBS_HOME; do
    if [[ -v "$key" ]]; then printf 'export %s=%q\n' "$key" "${!key}"; fi
  done > "$INSTALL_PREFIX/share/sai/runtime-env.sh"
  chmod -R a+rX,go-w "$INSTALL_PREFIX"
  python3 /control/qe_science.py inspect "$INSTALL_PREFIX" --identity "$delivery"
  python3 /control/cp2k_overlay_sync.py build
  ;;
export)
  test ! -e /workspace/export
  bash /control/create_rootfs.sh /workspace/export
  mkdir -p "/workspace/export$(dirname -- "$INSTALL_PREFIX")"
  cp -a "$INSTALL_PREFIX" "/workspace/export$INSTALL_PREFIX"
  mksquashfs /workspace/export /workspace/final.squashfs -noappend -all-root -no-xattrs -processors "$BUILD_JOBS"
  python3 /control/cp2k_overlay_sync.py export
  ;;
verify)
  python3 /control/delivery_layout.py prefix "$delivery" "$software" "$sha" "$version" "$target" --installed "$INSTALL_PREFIX/share/sai/release-identity.json"
  source "$INSTALL_PREFIX/share/sai/runtime-env.sh"
  python3 /control/qe_science.py inspect "$INSTALL_PREFIX"
  if touch "$INSTALL_PREFIX/.write-test" 2>/dev/null; then exit 1; fi
  ;;
*) exit 2 ;;
esac
