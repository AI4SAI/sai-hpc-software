#!/usr/bin/env bash
set -euo pipefail
umask 022
op=$1; software=$2; sha=$3; version=$4; target=$5; delivery=${6:?canonical identity required}
[[ "$software" == cp2k && "$sha" =~ ^[0-9a-f]{40}$ ]]
export PATH=/usr/bin:/bin TMPDIR="${TMPDIR:-/workspace/tmp}"
INSTALL_PREFIX=$(python3 /control/delivery_layout.py prefix "$delivery" "$software" "$sha" "$version" "$target")
export INSTALL_PREFIX
export CP2K_SOURCE_SHA="$sha"
case "$op" in
build)
  mkdir -p /workspace/tmp
  git init /workspace/source
  # Borrow only immutable Git objects from the read-only source cache. Mark the
  # exact requested commit shallow inside this overlay: the cache may contain
  # its full tree without its parents, and no unrelated refs/HEAD are trusted.
  printf '%s\n' /input/repository/objects > /workspace/source/.git/objects/info/alternates
  git -C /workspace/source cat-file -e "$sha^{commit}"
  printf '%s\n' "$sha" > /workspace/source/.git/shallow
  git -C /workspace/source -c core.hooksPath=/dev/null checkout --detach "$sha"
  test "$(git -C /workspace/source rev-parse HEAD)" = "$sha"
  source /control/environment.sh "$target"
  bash /control/cp2k_build.sh "$target"
  printf '%s\n' "$delivery" > "$INSTALL_PREFIX/share/sai/release-identity.json"
  ;;
metadata)
  test -x "$INSTALL_PREFIX/bin/cp2k.psmp"
  # Repacking must preserve the original, compute-node-resolved environment.
  # A fresh metadata process has no loaded modules and must not overwrite it.
  test "$(<"$INSTALL_PREFIX/share/sai/source-sha")" = "$sha"
  test "$(<"$INSTALL_PREFIX/share/sai/target")" = "$target"
  test -s "$INSTALL_PREFIX/share/sai/runtime-env.sh"
  test -s "$INSTALL_PREFIX/share/sai/modules.txt"
  test -s "$INSTALL_PREFIX/share/sai/CMakeCache.txt"
  python3 /control/delivery_layout.py prefix "$delivery" "$software" "$sha" "$version" "$target" \
    --installed "$INSTALL_PREFIX/share/sai/release-identity.json"
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
  # software_controller invokes this on result.sif without the build overlay.
  # The verifier and data are installed assets, not /workspace or /control code.
  python3 "$INSTALL_PREFIX/share/sai/cp2k_feature_contract.py" verify \
    "$INSTALL_PREFIX" "$target" "$sha" ;;
*) exit 2;; esac
