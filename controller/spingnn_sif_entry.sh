#!/bin/bash
set -euo pipefail
phase=$1
prefix=$2
test -n "${APPTAINER_CONTAINER:?SIF context required}"
export PYTHONDONTWRITEBYTECODE=1
case "$phase" in
  build)
    mkdir -p /workspace/tmp /workspace/recipe
    bash /control/spingnn_build.sh /workspace/recipe "$prefix" /control
    python3 /control/cp2k_overlay_sync.py build
    ;;
  export)
    bash /control/create_rootfs.sh /workspace/export
    mkdir -p "/workspace/export$(dirname "$prefix")"
    cp -a "$prefix" "/workspace/export$prefix"
    mksquashfs /workspace/export /workspace/final.squashfs -noappend -all-root -no-xattrs -processors 2
    python3 /control/cp2k_overlay_sync.py export
    ;;
  verify)
    if touch "$prefix/.unexpected-write" 2>/dev/null; then exit 1; fi
    bash /control/spingnn_accept.sh "$prefix" /results/container /control /input/site.sh container
    ;;
  *) exit 2 ;;
esac
