#!/usr/bin/env bash
set -euo pipefail
op=$1; software=$2; sha=$3; version=$4; target=$5
[[ "$software" == abacus && "$sha" =~ ^[0-9a-f]{40}$ ]]
[[ "$version" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]
[[ "$target" == cpu-misc || "$target" == v100 || "$target" == a100 ]]
export PATH=/usr/bin:/bin TMPDIR=/workspace/tmp
export INSTALL_PREFIX="/opt/software/$software/$version/$target"
case "$op" in
  build)
    mkdir -p /workspace/tmp
    test ! -e /home/stardust/.ssh
    test ! -w /input/repository
    test ! -w /control
    test ! -w /opt/devtools
    git init /workspace/source
    git -C /workspace/source -c core.hooksPath=/dev/null fetch --no-tags /input/repository "$sha"
    git -C /workspace/source -c core.hooksPath=/dev/null checkout --detach "$sha"
    test "$(git -C /workspace/source rev-parse HEAD)" = "$sha"
    source /control/environment.sh
    module -t list 2>&1
    bash /control/abacus_build.sh "$target"
    mkdir -p "$INSTALL_PREFIX/share/sai"
    printf '%s\n' "$sha" > "$INSTALL_PREFIX/share/sai/source-sha"
    module -t list > "$INSTALL_PREFIX/share/sai/modules.txt" 2>&1
    cp /workspace/build/CMakeCache.txt "$INSTALL_PREFIX/share/sai/"
    ;;
  export)
    # Staging and squashfs creation stay within the ext3 image.
    bash /control/create_rootfs.sh /workspace/export
    cp -a /opt/software /workspace/export/opt/software
    mksquashfs /workspace/export /workspace/final.squashfs -noappend -all-root -no-xattrs -processors "$BUILD_JOBS"
    ;;
  verify)
    source /control/environment.sh
    test "$(cat "$INSTALL_PREFIX/share/sai/source-sha")" = "$sha"
    "$INSTALL_PREFIX/bin/abacus" --info
    if ldd "$INSTALL_PREFIX/bin/abacus" | tee /dev/stderr | grep -q 'not found'; then exit 1; fi
    if touch "$INSTALL_PREFIX/.write-test" 2>/dev/null; then echo 'artifact must be read-only' >&2; exit 1; fi
    ;;
  *) exit 2;;
esac
