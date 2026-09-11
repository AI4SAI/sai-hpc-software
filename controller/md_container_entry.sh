#!/usr/bin/env bash
set -euo pipefail
phase=$1; version=$2; target=$3; dp_sha=$4; lmp_sha=$5
[[ "$version" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ && "$dp_sha" =~ ^[0-9a-f]{40}$ && "$lmp_sha" =~ ^[0-9a-f]{40}$ ]]
export DEEPMD_PREFIX="/opt/software/deepmd-kit/$version/$target"
export LAMMPS_PREFIX="/opt/software/lammps/$version/$target"
export PATH=/usr/bin:/bin TMPDIR=/workspace/tmp
case "$phase" in
  build)
    mkdir -p /workspace/tmp
    for name in deepmd-kit lammps; do
      if [[ "$name" == deepmd-kit ]]; then sha=$dp_sha; else sha=$lmp_sha; fi
      git init "/workspace/$name"
      git -C "/workspace/$name" -c core.hooksPath=/dev/null fetch --depth=1 --no-tags "/input/$name" "$sha"
      git -C "/workspace/$name" -c core.hooksPath=/dev/null checkout --detach "$sha"
      [[ "$(git -C "/workspace/$name" rev-parse HEAD)" == "$sha" ]]
    done
    bash /control/md_build.sh "$target"
    ;;
  export)
    bash /control/create_rootfs.sh /workspace/export
    mkdir -p /workspace/export/opt/apps /workspace/export/opt/software
    cp -a /opt/software/deepmd-kit /opt/software/lammps /workspace/export/opt/software/
    mksquashfs /workspace/export /workspace/final.squashfs -noappend -all-root -no-xattrs -processors "$BUILD_JOBS"
    ;;
  verify)
    source "$LAMMPS_PREFIX/share/sai/runtime-env.sh"
    "$DEEPMD_PREFIX/bin/dp" --version
    "$LAMMPS_PREFIX/bin/lmp" -h
    "$DEEPMD_PREFIX/bin/python" /control/md_relocate_audit.py "$DEEPMD_PREFIX" "$LAMMPS_PREFIX"
    ;;
  *) exit 2 ;;
esac
