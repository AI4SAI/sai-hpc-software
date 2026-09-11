#!/usr/bin/env bash
set -euo pipefail
umask 022
op=$1; software=$2; sha=$3; version=$4; target=$5; delivery=${6:?canonical identity required}
[[ "$software" == cp2k && "$sha" =~ ^[0-9a-f]{40}$ ]]
export PATH=/usr/bin:/bin TMPDIR=/workspace/tmp
INSTALL_PREFIX=$(python3 /control/delivery_layout.py prefix "$delivery" "$software" "$sha" "$version" "$target")
export INSTALL_PREFIX
export CP2K_SOURCE_SHA="$sha"
case "$op" in
build)
  mkdir -p /workspace/tmp
  git init /workspace/source
  git -C /workspace/source -c core.hooksPath=/dev/null fetch --depth=1 --no-tags /input/repository "$sha"
  git -C /workspace/source -c core.hooksPath=/dev/null checkout --detach "$sha"
  test "$(git -C /workspace/source rev-parse HEAD)" = "$sha"
  source /control/environment.sh "$target"
  bash /control/cp2k_build.sh "$target"
  printf '%s\n' "$delivery" > "$INSTALL_PREFIX/share/sai/release-identity.json"
  ;;
metadata)
  test -x "$INSTALL_PREFIX/bin/cp2k.psmp"
  mkdir -p "$INSTALL_PREFIX/share/sai"
  printf '%s\n' "$sha" > "$INSTALL_PREFIX/share/sai/source-sha"
  printf '%s\n' "$target" > "$INSTALL_PREFIX/share/sai/target"
  printf '%s\n' "$delivery" > "$INSTALL_PREFIX/share/sai/release-identity.json"
  module -t list > "$INSTALL_PREFIX/share/sai/modules.txt" 2>&1 || true
  cp /workspace/build/CMakeCache.txt "$INSTALL_PREFIX/share/sai/" 2>/dev/null || true
  ;;
export)
  rm -rf -- /workspace/export /workspace/final.squashfs
  bash /control/create_rootfs.sh /workspace/export
  mkdir -p /workspace/export/opt/software
  cp -a /opt/software/cp2k /workspace/export/opt/software/
  if [[ -d /opt/software/cp2k-dependencies ]]; then
    cp -a /opt/software/cp2k-dependencies /workspace/export/opt/software/
  fi
  chmod -R a+rX,u+w,go-w /workspace/export/opt/software
  mksquashfs /workspace/export /workspace/final.squashfs -noappend -all-root -no-xattrs -processors "$BUILD_JOBS"
  ;;
verify)
  python3 /control/delivery_layout.py prefix "$delivery" "$software" "$sha" "$version" "$target" \
    --installed "$INSTALL_PREFIX/share/sai/release-identity.json"
  source "$INSTALL_PREFIX/share/sai/runtime-env.sh"
  test -x "$INSTALL_PREFIX/bin/cp2k.psmp"
  "$INSTALL_PREFIX/bin/cp2k.psmp" --version
  dependencies=$(ldd "$INSTALL_PREFIX/bin/cp2k.psmp")
  printf '%s\n' "$dependencies"
  [[ "$dependencies" != *"not found"* ]] ;;
*) exit 2;; esac
