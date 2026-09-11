#!/usr/bin/env bash
# Run inside the CP2K build container after loading its compiler/BLAS modules.
set -euo pipefail
cache=${1:?source cache directory required}
work=${2:?dependency build directory required}
prefix=${3:?dependency install prefix required}
jobs=${BUILD_JOBS:-8}
native_flags=${CP2K_NATIVE_FLAGS:--O3 -march=native}
mkdir -p "$work" "$prefix"
export CMAKE_PREFIX_PATH="$prefix${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:$prefix/lib64/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"

build_dependency() {
  local name=$1 version=$2 checksum=$3
  local archive="$cache/$name-$version.tar.gz"
  printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check --status
  printf '%s  %s\n' "$checksum" "$archive" >> "$work/source-archives.sha256"
  mkdir -p "$work/$name-source"
  tar --no-same-owner --strip-components=1 -xzf "$archive" -C "$work/$name-source"
  cmake -S "$work/$name-source" -B "$work/$name-build" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix" \
    -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_C_FLAGS="$native_flags" -DCMAKE_CXX_FLAGS="$native_flags" -DCMAKE_Fortran_FLAGS="$native_flags" \
    -DCMAKE_INSTALL_RPATH='$ORIGIN/../lib' -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=FALSE \
    -DBUILD_SHARED_LIBS=OFF -DBUILD_TESTING=OFF -DWITH_TESTS=OFF \
    -DTBLITE_WITH_TESTS=OFF -D"$name-dependency-method=cmake" \
    -DFETCHCONTENT_FULLY_DISCONNECTED=ON
  cmake --build "$work/$name-build" --parallel "$jobs"
  cmake --install "$work/$name-build"
}

build_dependency toml-f 0.5.1 faad67f45912e92641c1daa3c5267482df243ee96be916c8b703a5034afe1885
build_dependency mctc-lib 0.5.2 25b2a3d18343079e92449b9c8d73a23fa8dc0ede2faf5c94ad6cfa4676355133
build_dependency multicharge 0.5.0 6b137db34c89ab73f8cc8f2849db1679b2efa1fdec255cd77fca672d7fc8ea5c
build_dependency s-dftd3 1.4.0 c548629115c3d5f180d06a70bc29dcf42e4018fbc9e4ba7c99abc1cdbfda7c1e
build_dependency mstore 0.3.0 56b3d778629eb74b8a515cd53c727d04609f858a07f8d3555fd5fd392a206dcc
build_dependency dftd4 4.2.0 e1255317b33af5326faf605e6135d0e0b15935a36304b8d59b7142ca15110959
build_dependency tblite 0.7.0 7864755e3faeef43a2f334a1679f6ece525eb604837e772c6623c042214ea39f
