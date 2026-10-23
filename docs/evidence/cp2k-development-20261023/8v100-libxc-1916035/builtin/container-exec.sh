#!/usr/bin/env bash
set -euo pipefail
driver=${LD_LIBRARY_PATH:-}
source "$1"
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH}${driver:+:$driver}"
export HOME=/runtime
prefix=${2%/bin/cp2k.psmp}
if [[ "$SAI_LIBXC_MODE" == inspect ]]; then
  "$2" --version > /work/version.txt
  ldd "$2" > /work/ldd.txt
  cp "$prefix/share/sai/CMakeCache.txt" /work/CMakeCache.txt
  cp "$prefix/share/sai/libxc-source-archives.sha256" /work/libxc-source-archives.sha256
  shopt -s nullglob
  libraries=("$prefix"/dependencies/libxc/lib/libxc*.so*)
  : > /work/libxc-libraries.sha256
  if (( ${#libraries[@]} )); then
    sha256sum "${libraries[@]}" > /work/libxc-libraries.sha256
  fi
elif [[ "$SAI_LIBXC_MODE" == device ]]; then
  "/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/profilers/12.9/Nsight_Systems_2025.3/bin/nsys" --version > /work/nsys-version.txt
  "/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/profilers/12.9/Nsight_Systems_2025.3/bin/nsys" profile --trace=cuda --sample=none --cpuctxsw=none \
    --output=/work/libxc-device /usr/bin/bash /work/cp2k-exec.sh "$2" "${@:3}"
  "/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/profilers/12.9/Nsight_Systems_2025.3/bin/nsys" export --type=sqlite --output=/work/libxc-device.sqlite /work/libxc-device.nsys-rep
else
  exec /usr/bin/bash /work/cp2k-exec.sh "$2" "${@:3}"
fi
