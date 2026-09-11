#!/usr/bin/env bash
set -euo pipefail
op=$1; software=$2; sha=$3; version=$4; target=$5
[[ "$software" == gpumd && "$sha" =~ ^[0-9a-f]{40}$ ]]
[[ "$version" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]
export PATH=/usr/bin:/bin TMPDIR=/workspace/tmp
export INSTALL_PREFIX="/opt/software/gpumd/$version/$target"
case "$op" in
build)
  mkdir -p /workspace/tmp
  test ! -w /control
  test ! -w /input/repository
  test ! -w /opt/apps
  git init /workspace/source
  git -C /workspace/source -c core.hooksPath=/dev/null fetch --depth=1 --no-tags /input/repository "$sha"
  git -C /workspace/source -c core.hooksPath=/dev/null checkout --detach "$sha"
  test "$(git -C /workspace/source rev-parse HEAD)" = "$sha"
  source /control/gpumd_environment.sh "$target"
  bash /control/gpumd_build.sh
  printf '%s\n' "$sha" > "$INSTALL_PREFIX/share/sai/source-sha"
  printf '%s\n' "$target" > "$INSTALL_PREFIX/share/sai/target"
  module -t list > "$INSTALL_PREFIX/share/sai/modules.txt" 2>&1
  nvcc --version > "$INSTALL_PREFIX/share/sai/compiler.txt"
  gcc -march=native -Q --help=target > "$INSTALL_PREFIX/share/sai/native-cpu-flags.txt"
  lscpu > "$INSTALL_PREFIX/share/sai/hardware.txt"
  for key in PATH LD_LIBRARY_PATH CUDA_HOME CUDA_PATH DEEPMD_ROOT DEEPMD_PYTHON PLUMED_KERNEL; do
    if [[ -v "$key" ]]; then printf 'export %s=%q\n' "$key" "${!key}"; fi
  done > "$INSTALL_PREFIX/share/sai/runtime-env.sh"
  # bash resolves this path relative to the installed script, including after
  # copying the prefix to physical /opt or to another validation mount point.
  printf '%s\n' 'gpumd_installed_prefix=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)' \
    'export GPUMD_SRC="$gpumd_installed_prefix/share/gpumd/src" PATH="$gpumd_installed_prefix/bin:$PATH"' \
    'export CUDACXX="$CUDA_HOME/bin/nvcc"' \
    >> "$INSTALL_PREFIX/share/sai/runtime-env.sh"
  ;;
export)
  test ! -e /workspace/export
  bash /control/create_rootfs.sh /workspace/export
  cp -a /opt/software /workspace/export/opt/software
  mksquashfs /workspace/export /workspace/final.squashfs -noappend -all-root -no-xattrs -processors "$BUILD_JOBS"
  ;;
verify)
  source "$INSTALL_PREFIX/share/sai/runtime-env.sh"
  test "$(cat "$INSTALL_PREFIX/share/sai/source-sha")" = "$sha"
  test -s "$GPUMD_SRC/main_nep/nep_specialized.cu"
  python3 /control/gpumd_science.py inspect "$INSTALL_PREFIX"
  if touch "$INSTALL_PREFIX/.write-test" 2>/dev/null; then exit 1; fi
  ;;
*) exit 2 ;;
esac
