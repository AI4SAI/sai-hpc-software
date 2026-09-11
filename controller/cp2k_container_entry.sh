#!/usr/bin/env bash
set -euo pipefail
op=$1; software=$2; sha=$3; version=$4; target=$5
[[ "$software" == cp2k && "$sha" =~ ^[0-9a-f]{40}$ ]]
[[ "$version" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]
[[ "$target" == dsprhbm || "$target" == 4v100-avx512 || "$target" == 16v100-avx2 || "$target" == 8v100v0-avx512 ]]
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
  # Repacking must preserve the original, compute-node-resolved environment.
  # A fresh metadata process has no loaded modules and must not overwrite it.
  test "$(<"$INSTALL_PREFIX/share/sai/source-sha")" = "$sha"
  test "$(<"$INSTALL_PREFIX/share/sai/target")" = "$target"
  test -s "$INSTALL_PREFIX/share/sai/runtime-env.sh"
  test -s "$INSTALL_PREFIX/share/sai/modules.txt"
  test -s "$INSTALL_PREFIX/share/sai/CMakeCache.txt"
  ;;
export)
  rm -rf -- /workspace/export /workspace/final.squashfs
  bash /control/create_rootfs.sh /workspace/export
  mkdir -p /workspace/export/opt/software
  cp -a /opt/software/cp2k /workspace/export/opt/software/
  if [[ -d /opt/software/cp2k-dependencies ]]; then
    cp -a /opt/software/cp2k-dependencies /workspace/export/opt/software/
  fi
  mksquashfs /workspace/export /workspace/final.squashfs -noappend -all-root -no-xattrs -processors "$BUILD_JOBS"
  ;;
verify)
  source "$INSTALL_PREFIX/share/sai/runtime-env.sh"
  # software_controller invokes this on result.sif without the build overlay.
  # The verifier and data are installed assets, not /workspace or /control code.
  python3 "$INSTALL_PREFIX/share/sai/cp2k_feature_contract.py" verify \
    "$INSTALL_PREFIX" "$target" "$sha" ;;
*) exit 2;; esac
