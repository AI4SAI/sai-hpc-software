#!/usr/bin/env bash
# Run inside the CP2K build container after loading its compiler/BLAS modules.
set -euo pipefail
cache=${1:?source cache directory required}
work=${2:?dependency build directory required}
prefix=${3:?dependency install prefix required}
cp2k_source=${4:?pinned CP2K source directory required}
updates=${5:?verified source updates directory required}
jobs=${BUILD_JOBS:-8}
native_flags=${CP2K_NATIVE_FLAGS:--O3 -march=native -mtune=native}
toolchain="$cp2k_source/tools/toolchain/scripts/stage8/install_tblite.sh"
# Read the declaration without executing the upstream installer. Release and
# development use different Fortran APIs and must not share a hard-coded pin.
tblite_version=$(sed -nE 's/^[[:space:]]*tblite_ver="([0-9]+\.[0-9]+\.[0-9]+)"[[:space:]]*$/\1/p' "$toolchain")
case "$tblite_version" in
  0.6.0|0.7.0) ;;
  *) echo "unsupported or missing CP2K TBLITE version: $tblite_version" >&2; exit 1 ;;
esac
mkdir -p "$work" "$prefix"
export CMAKE_PREFIX_PATH="$prefix${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig:$prefix/lib64/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
sha256sum "$toolchain" >> "$work/source-archives.sha256"
printf 'CP2K_TBLITE_VERSION %s\n' "$tblite_version"

build_source() {
  local name=$1 source=$2
  local extra=()
  if [[ "$tblite_version" == 0.6.0 && "$name" == mstore ]]; then
    # The bundled mctc-lib export references OpenMP but fails to discover its
    # imported target. Define it after mstore's project() enables Fortran,
    # without modifying either upstream source or disabling OpenMP.
    printf '%s\n' 'find_package(OpenMP REQUIRED COMPONENTS Fortran)' > "$work/mstore-openmp.cmake"
    sha256sum "$work/mstore-openmp.cmake" >> "$work/source-archives.sha256"
    extra+=(-DCMAKE_PROJECT_mstore_INCLUDE:FILEPATH="$work/mstore-openmp.cmake")
  fi
  cmake -S "$source" -B "$work/$name-build" \
    -DCMAKE_C_COMPILER="${CC:?explicit CP2K C compiler required}" \
    -DCMAKE_CXX_COMPILER="${CXX:?explicit CP2K C++ compiler required}" \
    -DCMAKE_Fortran_COMPILER="${FC:?explicit CP2K Fortran compiler required}" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix" \
    -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_C_FLAGS="$native_flags" -DCMAKE_CXX_FLAGS="$native_flags" -DCMAKE_Fortran_FLAGS="$native_flags" \
    -DCMAKE_INSTALL_RPATH='$ORIGIN/../lib' -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=FALSE \
    -DBUILD_SHARED_LIBS=OFF -DBUILD_TESTING=OFF -DWITH_TESTS=OFF \
    -DTBLITE_WITH_TESTS=OFF -D"$name-dependency-method=cmake" \
    -DFETCHCONTENT_FULLY_DISCONNECTED=ON "${extra[@]}"
  cmake --build "$work/$name-build" --parallel "$jobs"
  cmake --install "$work/$name-build"
}

build_dependency() {
  local name=$1 version=$2 checksum=$3
  local archive="$cache/$name-$version.tar.gz"
  printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check --status
  printf '%s  %s\n' "$checksum" "$archive" >> "$work/source-archives.sha256"
  mkdir -p "$work/$name-source"
  tar --no-same-owner --strip-components=1 -xzf "$archive" -C "$work/$name-source"
  build_source "$name" "$work/$name-source"
}

if [[ "$tblite_version" == 0.6.0 ]]; then
  # CP2K's complete release archive includes the matching subproject sources;
  # its gradient fixes do not apply to the standalone D3/D4 GitHub tag archives.
  archive="$updates/tblite-0.6.0.tar.xz"
  checksum=372281aedb89234168d00eb691addb303197a9462a9c55d145c835f2cf5e8b42
  printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check --status
  printf '%s  %s\n' "$checksum" "$archive" >> "$work/source-archives.sha256"
  mkdir -p "$work/tblite-source"
  tar --no-same-owner --strip-components=1 -xJf "$archive" -C "$work/tblite-source"
  for entry in \
    's-dftd3 simple-dftd3-1.4.0-gradient-fixes.patch 6a20b4622821018954a204a33dcb2128ddfaec04a37f3b4a625a71058ba2a40b' \
    'dftd4 dftd4-4.2.0-gradient-fixes.patch 5335cb7d02a8c3141f28967ef61418f425fb1e638d51fe75618b700321d9087a'; do
    read -r name patch_name checksum <<< "$entry"
    patch_file="$(dirname "$toolchain")/$patch_name"
    printf '%s  %s\n' "$checksum" "$patch_file" | sha256sum --check --status
    printf '%s  %s\n' "$checksum" "$patch_file" >> "$work/source-archives.sha256"
    patch --batch --forward -l -d "$work/tblite-source/subprojects/$name" -p1 < "$patch_file"
  done
  # This bundled DFT-D4 version discovers mstore even with tests disabled.
  for name in toml-f mctc-lib multicharge s-dftd3 mstore dftd4; do
    build_source "$name" "$work/tblite-source/subprojects/$name"
  done
  build_source tblite "$work/tblite-source"
  exit 0
fi

build_dependency toml-f 0.5.1 faad67f45912e92641c1daa3c5267482df243ee96be916c8b703a5034afe1885
build_dependency mctc-lib 0.5.2 25b2a3d18343079e92449b9c8d73a23fa8dc0ede2faf5c94ad6cfa4676355133
build_dependency multicharge 0.5.0 6b137db34c89ab73f8cc8f2849db1679b2efa1fdec255cd77fca672d7fc8ea5c
build_dependency s-dftd3 1.4.0 c548629115c3d5f180d06a70bc29dcf42e4018fbc9e4ba7c99abc1cdbfda7c1e
build_dependency mstore 0.3.0 56b3d778629eb74b8a515cd53c727d04609f858a07f8d3555fd5fd392a206dcc
build_dependency dftd4 4.2.0 e1255317b33af5326faf605e6135d0e0b15935a36304b8d59b7142ca15110959
build_dependency tblite 0.7.0 7864755e3faeef43a2f334a1679f6ece525eb604837e772c6623c042214ea39f
