#!/usr/bin/env bash
# New prefixes are written only inside the project-backed build overlay.
set -euo pipefail
umask 022
phase=${1:?}
export PATH=/usr/bin:/bin
case "$phase" in
  build)
    mkdir -p /workspace/tmp /workspace/receipts
    export TMPDIR=/workspace/tmp
    source /control/qe_environment.sh dsprhbm
    export INSTALL_PREFIX=/workspace/receipts
    export QE_HDF5_INSTALL_PREFIX=$(python3 /control/app_dependency.py prefix qe-hdf5)
    /usr/bin/time -v -o /results/hdf5-build.time bash /control/qe_hdf5.sh
    mkdir -p "$QE_HDF5_INSTALL_PREFIX/share/licenses/hdf5"
    cp /workspace/hdf5-source/COPYING "$QE_HDF5_INSTALL_PREFIX/share/licenses/hdf5/"
    python3 /control/app_dependency.py record-hdf5
    /usr/bin/time -v -o /results/torch-prepare.time python3 /control/app_dependency.py prepare-torch
    bash /control/create_rootfs.sh /workspace/export
    python3 /control/app_dependency.py export
    mksquashfs /workspace/export /workspace/final.squashfs -noappend -all-root -no-xattrs -processors "$BUILD_JOBS"
    python3 /control/cp2k_overlay_sync.py dependencies
    ;;
  verify)
    export TMPDIR=/runtime
    source /control/qe_environment.sh dsprhbm
    python3 /control/app_dependency.py verify-hdf5
    source /control/environment.sh dsprhbm
    python3 /control/app_dependency.py verify-torch
    ;;
  verify-torch)
    export TMPDIR=/runtime
    source /control/environment.sh "${2:?target required}"
    python3 /control/app_dependency.py verify-torch
    ;;
  *) exit 2 ;;
esac
