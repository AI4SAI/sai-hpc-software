#!/usr/bin/env bash
set -euo pipefail
umask 022
phase=${1:?}; partition=${2:?}
case "$partition" in
  16V100) target=16v100-avx2 ;;
  4V100) target=4v100-avx512 ;;
  8V100V0) target=8v100v0-avx512 ;;
  DSPRHBM) target=dsprhbm ;;
  *) exit 2 ;;
esac
case "$phase" in
  build)
    bash /control/cp2k_spack_dependencies.sh "$partition" /opt/devtools devtools
    bash /control/create_rootfs.sh /workspace/export
    python3 /control/cp2k_devtools.py stage
    mksquashfs /workspace/export /workspace/final.squashfs \
      -noappend -all-root -no-xattrs -processors "$BUILD_JOBS"
    python3 /control/cp2k_overlay_sync.py dependencies
    ;;
  verify)
    source /control/environment.sh "$target" cp2k
    module unload gcc
    module load gcc/14.3.0
    python3 /control/cp2k_devtools.py verify /results/provider.json /results/sif-consumer
    ;;
  *) exit 2 ;;
esac
