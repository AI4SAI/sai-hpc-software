#!/usr/bin/env bash
# CPU QE uses MPI's legacy Fortran module. NVHPC rejects its combination with
# HDF5's optional mpi_f08 overloads; retain parallel I/O through the F90 interface.
set -euo pipefail
archive=/input/qe-dependencies/hdf5-1.14.6.tar.gz
printf '%s  %s\n' e4defbac30f50d64e1556374aa49e574417c9e72c6b1de7a4ff88c4b1bea6e9b "$archive" | sha256sum --check --status
mkdir /workspace/hdf5-source
tar -xzf "$archive" -C /workspace/hdf5-source --strip-components=2 --no-same-owner
python3 - <<'PY'
import hashlib
import json
from pathlib import Path
path = Path('/workspace/hdf5-source/CMakeLists.txt')
before = path.read_text()
old = 'if (MPI_Fortran_HAVE_F08_MODULE)'
if before.count(old) != 1:
    raise ValueError('HDF5 MPI interface configuration changed')
after = before.replace(old,
    'option (HDF5_ENABLE_MPI_F08 "Build optional MPI Fortran 2008 overloads" ON)\n'
    '      if (MPI_Fortran_HAVE_F08_MODULE AND HDF5_ENABLE_MPI_F08)')
path.write_text(after)
Path('/workspace/hdf5-mpi-interface.json').write_text(json.dumps(dict(
    file='CMakeLists.txt', before_sha256=hashlib.sha256(before.encode()).hexdigest(),
    after_sha256=hashlib.sha256(after.encode()).hexdigest(),
    description='Select the F90 parallel-I/O interface used by QE; omit optional mpi_f08 overloads.'),
    sort_keys=True)+'\n')
PY
export HDF5_ROOT=${QE_HDF5_INSTALL_PREFIX:-"$INSTALL_PREFIX/deps/hdf5"}
cmake -S /workspace/hdf5-source -B /workspace/hdf5-build \
  -DCMAKE_INSTALL_PREFIX="$HDF5_ROOT" -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=mpicc -DCMAKE_Fortran_COMPILER=mpifort \
  '-DCMAKE_C_FLAGS_RELEASE=-O3 -tp=host' '-DCMAKE_Fortran_FLAGS_RELEASE=-O3 -tp=host' \
  -DBUILD_SHARED_LIBS=OFF -DBUILD_TESTING=OFF -DHDF5_BUILD_TOOLS=OFF \
  -DHDF5_BUILD_EXAMPLES=OFF -DHDF5_BUILD_CPP_LIB=OFF -DHDF5_BUILD_FORTRAN=ON \
  -DHDF5_BUILD_HL_LIB=ON -DHDF5_ENABLE_PARALLEL=ON \
  -DHDF5_ENABLE_MPI_F08=OFF \
  -DHDF5_ENABLE_Z_LIB_SUPPORT=ON -DHDF5_ENABLE_SZIP_SUPPORT=ON \
  -DLIBAEC_PACKAGE_NAME=qe_szip -Dqe_szip_DIR=/control
grep -q '^HDF5_ENABLE_SZIP_SUPPORT:BOOL=ON$' /workspace/hdf5-build/CMakeCache.txt
cmake --build /workspace/hdf5-build --parallel "$BUILD_JOBS"
cmake --install /workspace/hdf5-build
grep -q '^#define CMAKE_H5_HAVE_MPI_F08 0$' "$HDF5_ROOT/include/H5config_f.inc"
export LD_LIBRARY_PATH="$HDF5_ROOT/lib:${LD_LIBRARY_PATH:-}"
export PATH="$HDF5_ROOT/bin:$PATH"
mkdir -p "$INSTALL_PREFIX/share/sai"
cp /workspace/hdf5-build/CMakeCache.txt "$INSTALL_PREFIX/share/sai/hdf5-CMakeCache.txt"
cp /workspace/hdf5-mpi-interface.json "$INSTALL_PREFIX/share/sai/"
printf '%s\n' e4defbac30f50d64e1556374aa49e574417c9e72c6b1de7a4ff88c4b1bea6e9b > "$INSTALL_PREFIX/share/sai/hdf5-source-sha256"
