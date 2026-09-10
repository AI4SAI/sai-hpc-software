#!/usr/bin/env bash
set -euo pipefail
op=$1; software=$2; sha=$3; version=$4; target=$5
[[ "$software" == cp2k && "$sha" =~ ^[0-9a-f]{40}$ ]]
[[ "$version" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]
[[ "$target" == dsprhbm || "$target" == 4v100-avx512 || "$target" == 16v100-avx2 || "$target" == a100 ]]
export PATH=/usr/bin:/bin TMPDIR=/workspace/tmp INSTALL_PREFIX="/opt/software/$software/$version/$target"
export CP2K_SOURCE_SHA="$sha"
case "$op" in
build)
  mkdir -p /workspace/tmp
  git init /workspace/source
  git -C /workspace/source -c core.hooksPath=/dev/null fetch --depth=1 --no-tags /input/repository "$sha"
  git -C /workspace/source -c core.hooksPath=/dev/null checkout --detach "$sha"
  test "$(git -C /workspace/source rev-parse HEAD)" = "$sha"
  source /control/environment.sh "$target"
  bash /control/cp2k_build.sh "$target" ;;
metadata)
  test -x "$INSTALL_PREFIX/bin/cp2k.psmp"
  mkdir -p "$INSTALL_PREFIX/share/sai"
  printf '%s\n' "$sha" > "$INSTALL_PREFIX/share/sai/source-sha"
  printf '%s\n' "$target" > "$INSTALL_PREFIX/share/sai/target"
  module -t list > "$INSTALL_PREFIX/share/sai/modules.txt" 2>&1 || true
  cp /workspace/build/CMakeCache.txt "$INSTALL_PREFIX/share/sai/" 2>/dev/null || true
  ;;
export)
  rm -rf -- /workspace/export /workspace/final.squashfs
  bash /control/create_rootfs.sh /workspace/export
  cp -a /opt/software/cp2k /workspace/export/opt/software/
  if [[ -d /opt/software/cp2k-dependencies ]]; then
    cp -a /opt/software/cp2k-dependencies /workspace/export/opt/software/
  fi
  mksquashfs /workspace/export /workspace/final.squashfs -noappend -all-root -no-xattrs -processors "$BUILD_JOBS"
  ;;
verify)
  source "$INSTALL_PREFIX/share/sai/runtime-env.sh"
  test -x "$INSTALL_PREFIX/bin/cp2k.psmp"
  "$INSTALL_PREFIX/bin/cp2k.psmp" --version
  ldd "$INSTALL_PREFIX/bin/cp2k.psmp" | tee /tmp/ldd
  ! grep -q 'not found' /tmp/ldd ;;
*) exit 2;; esac
