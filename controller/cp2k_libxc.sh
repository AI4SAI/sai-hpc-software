#!/usr/bin/env bash
# Select the pinned CP2K source's LibXC inside its build container.
set -euo pipefail
cp2k_source=${1:?pinned CP2K source directory required}
updates=${2:?verified source updates directory required}
work=${3:?LibXC build directory required}
prefix=${4:?private LibXC prefix required}
accel=${5:?CP2K accelerator required}
gpu_arch=${6:-}
toolchain="$cp2k_source/tools/toolchain/scripts/stage3/install_libxc.sh"
version=$(sed -nE 's/^[[:space:]]*libxc_ver="([0-9]+\.[0-9]+\.[0-9]+)"[[:space:]]*$/\1/p' "$toolchain")
checksum=$(sed -nE 's/^[[:space:]]*libxc_sha256="([0-9a-f]{64})"[[:space:]]*$/\1/p' "$toolchain")
case "$version:$checksum" in
  7.0.0:e9ae69f8966d8de6b7585abd9fab588794ada1fab8f689337959a35abbf9527d) ;;
  7.1.2:3915fac94416e4c415534223ea492ad2663f928acf27e98662c861b094a6c306) ;;
  *) echo "unsupported or missing CP2K LibXC version/checksum: $version" >&2; exit 1 ;;
esac
case "$accel:$gpu_arch" in
  NONE:|CUDA:70) ;;
  *) echo "unsupported CP2K LibXC accelerator: $accel/$gpu_arch" >&2; exit 1 ;;
esac
mkdir -p "$work"
sha256sum "$toolchain" > "$work/source-archives.sha256"

if [[ "$version" == 7.0.0 ]]; then
  # Preserve the already accepted 2026.2 site-library selection. Its GPU
  # backend predates LibXC's CUDA support and must keep the CPU library.
  selected=${LIBXC_ROOT:?site LibXC prefix required for CP2K 2026.2}
  [[ -f "$selected/lib/cmake/Libxc/LibxcConfig.cmake" ]]
  grep -Eq '^set\(PACKAGE_VERSION "7\.0\.0"\)' \
    "$selected/lib/cmake/Libxc/LibxcConfigVersion.cmake"
  printf 'CP2K_LIBXC_PROVIDER site %s %s\n' "$version" "$selected"
else
  # Development needs LibXC >=7.1 and its matching CUDA API. Follow the
  # upstream toolchain's one-backend build, including Fortran and MAXORDER=3.
  # The archive arrives from the runner; compute nodes never fetch sources.
  archive="$updates/libxc-$version.tar.bz2"
  printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check --status
  printf '%s  %s\n' "$checksum" "$archive" >> "$work/source-archives.sha256"
  mkdir -p "$work/source"
  tar --no-same-owner --strip-components=1 -xjf "$archive" -C "$work/source"
  flags=${CP2K_NATIVE_FLAGS:--O3 -march=native -mtune=native}
  args=(
    -S "$work/source" -B "$work/build"
    -DCMAKE_C_COMPILER="${CC:?explicit CP2K C compiler required}"
    -DCMAKE_CXX_COMPILER="${CXX:?explicit CP2K C++ compiler required}"
    -DCMAKE_Fortran_COMPILER="${FC:?explicit CP2K Fortran compiler required}"
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix"
    -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_POSITION_INDEPENDENT_CODE=ON
    -DCMAKE_C_FLAGS="$flags -fno-var-tracking"
    -DCMAKE_CXX_FLAGS="$flags" -DCMAKE_Fortran_FLAGS="$flags"
    -DCMAKE_INSTALL_RPATH='$ORIGIN' -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=FALSE
    -DBUILD_TESTING=OFF -DENABLE_FORTRAN=ON -DMAXORDER=3
  )
  if [[ "$accel" == CUDA ]]; then
    args+=(-DBUILD_SHARED_LIBS=ON -DENABLE_CUDA=ON
           -DCMAKE_CUDA_ARCHITECTURES="$gpu_arch" -DCMAKE_CUDA_HOST_COMPILER="$CXX")
  else
    args+=(-DBUILD_SHARED_LIBS=OFF -DENABLE_CUDA=OFF)
  fi
  cmake "${args[@]}"
  cmake --build "$work/build" --parallel "${BUILD_JOBS:-8}"
  cmake --install "$work/build"
  [[ -f "$prefix/lib/cmake/Libxc/LibxcConfig.cmake" ]]
  selected=$prefix
  printf 'CP2K_LIBXC_PROVIDER private %s %s %s\n' "$version" "$selected" "$accel"
fi
# The caller sources only this generated assignment, not upstream shell code.
printf 'export LIBXC_ROOT=%q\n' "$selected" > "$work/environment.sh"
