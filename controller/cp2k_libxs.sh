#!/usr/bin/env bash
# Build the LibXS version declared by the exact CP2K source, offline.
set -euo pipefail
cp2k_source=${1:?pinned CP2K source directory required}
cache=${2:?verified release dependency cache required}
updates=${3:?verified source updates directory required}
work=${4:?LibXS build directory required}
prefix=${5:?private LibXS prefix required}
toolchain="$cp2k_source/tools/toolchain/scripts/stage4/install_libxs.sh"
version=$(sed -nE 's/^[[:space:]]*libxs_ver="([a-zA-Z0-9._-]+)"[[:space:]]*$/\1/p' "$toolchain")
checksum=$(sed -nE 's/^[[:space:]]*libxs_sha256="([0-9a-f]{64})"[[:space:]]*$/\1/p' "$toolchain")
case "$version:$checksum" in
  1.0.0:de26f50cb986a2f0e4f92c0eb489d40a44f7e4c5acd22751a6cfa2829dabd04d)
    archive="$cache/libxs-$version.tar.gz" ;;
  930a2c4:552f9dd5af66b593d81aeb5aeb8acedba01943ae8d0c1bc036ea79f5fa591e88)
    archive="$updates/libxs-$version.tar.gz" ;;
  *) echo "unsupported or missing CP2K LibXS version/checksum: $version" >&2; exit 1 ;;
esac
# Intermediate development commits used 1.0.0 plus an upstream repair. Newer
# LibXS contains that repair, so a missing patch must never select bare 1.0.0.
libxs_patch="$(dirname "$toolchain")/libxs-$version-jit-handle.patch"
if grep -Fq 'jit-handle.patch' "$toolchain" && [[ ! -f "$libxs_patch" ]]; then
  echo "CP2K toolchain declares a missing LIBXS patch: $libxs_patch" >&2
  exit 1
fi
printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check --status
mkdir -p "$work/source"
sha256sum "$toolchain" "$archive" > "$work/source-archives.sha256"
tar --no-same-owner --strip-components=1 -xzf "$archive" -C "$work/source"
if [[ -f "$libxs_patch" ]]; then
  patch --batch --forward -d "$work/source" -p1 < "$libxs_patch"
  sha256sum "$libxs_patch" >> "$work/source-archives.sha256"
fi
flags=${CP2K_NATIVE_FLAGS:--O3 -march=native -mtune=native}
cmake -S "$work/source" -B "$work/build" \
  -DCMAKE_C_COMPILER="${CC:?explicit CP2K C compiler required}" \
  -DCMAKE_CXX_COMPILER="${CXX:?explicit CP2K C++ compiler required}" \
  -DCMAKE_Fortran_COMPILER="${FC:?explicit CP2K Fortran compiler required}" \
  -DCMAKE_INSTALL_PREFIX="$prefix" -DCMAKE_INSTALL_LIBDIR=lib \
  -DCMAKE_BUILD_TYPE=Release -DLIBXS_FORTRAN=ON -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
  -DCMAKE_C_FLAGS="$flags" -DCMAKE_Fortran_FLAGS="$flags"
cmake --build "$work/build" --parallel "${BUILD_JOBS:-8}"
cmake --install "$work/build"
printf 'CP2K_LIBXS_VERSION %s %s\n' "$version" "$checksum"
