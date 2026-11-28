#!/bin/bash
# Run only inside the SIF overlay; sources and offline wheels are pinned inputs.
set -eo pipefail
run=$1
prefix=$2
control=$3
test -n "${APPTAINER_CONTAINER:?container build required}"
test "${SAI_SPINGNN_CONTEXT:?}" = build
export PATH=/usr/bin:/bin LD_LIBRARY_PATH='' LD_PRELOAD=''
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/devtools
# These exact modules and paths are rendered from spingnn_site.json.
source "$run/input/site.sh"
set -u
umask 022
cd "$run"
test "${SLURM_JOB_PARTITION:?}" = "$SAI_SPINGNN_PARTITION"
test "${MPI_HOME:?}" = "$SAI_SPINGNN_MPI"
test ! -e "$prefix"
mkdir -p "$(dirname "$prefix")"
mkdir "$prefix"
mkdir -p build acceptance
export PYTHONDONTWRITEBYTECODE=1
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 OMP_PROC_BIND=close OMP_PLACES=cores
export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher
export OMPI_CC="$CC" OMPI_CXX="$CXX"
export NVCC_WRAPPER_DEFAULT_COMPILER="$CXX" CXXFLAGS=-march=native
export OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1
/usr/bin/python3 -c 'import sys; sys.path.insert(0,sys.argv[1]); from spingnn_inputs import unpack; unpack(sys.argv[2],sys.argv[3])' "$control" "$run" "$SAI_SPINGNN_SOURCE_SHA"
src="$run/sources/spingnn2"
/usr/bin/python3 "$control/spingnn_source.py" "$src" "$SAI_SPINGNN_CUDA_ARCH" > evidence/source-adaptation.json
module -t list > evidence/modules.txt 2>&1
lscpu > evidence/lscpu.txt
mpicxx --showme > evidence/mpi-wrapper.txt
readlink -f "$(command -v mpirun)" > evidence/mpirun-path.txt
sha256sum "$MPI_HOME/lib/libmpi.so" > evidence/mpi-sha256.txt
ldd "$MPI_HOME/lib/libmpi.so" > evidence/mpi-ldd.txt
native_opts=(--mkl-root "$SPINGNN_MKL")
if [[ $SAI_SPINGNN_BACKEND == cuda ]]; then
  native_opts=(--arch "sm$SAI_SPINGNN_CUDA_ARCH" --cuda-root "$CUDA_HOME")
  nvcc --version > evidence/nvcc.txt
  nvidia-smi -q > evidence/nvidia-smi.txt
fi
/usr/bin/python3 "$src/tools/native/build_native.py" --backend "$SAI_SPINGNN_BACKEND" \
  --kokkos-source "$run/sources/kokkos-4.7.02" --json-source "$run/sources/json-3.11.3" \
  --cxx "$CXX" --cmake "$SPINGNN_CMAKE" "${native_opts[@]}" \
  --build-dir "$run/build/native" --jobs 2
# Preserve site dependencies while using self-relative paths for owned ELF libs.
# Pin lib explicitly: a minimal image lacks the host distro markers that make
# GNUInstallDirs choose lib instead of lib64. Install only after this configure.
"$SPINGNN_CMAKE" -S "$src/libspingnn2" -B "$run/build/native" \
  -DCMAKE_INSTALL_PREFIX="$prefix" -DCMAKE_INSTALL_LIBDIR=lib \
  -DCMAKE_INSTALL_RPATH="\$ORIGIN;$SPINGNN_GCC/lib64;${CUDA_HOME:-/nonexistent}/lib64;$SPINGNN_MKL/lib"
"$SPINGNN_CMAKE" --build "$run/build/native" --parallel 2
"$SPINGNN_CMAKE" --install "$run/build/native" --prefix "$prefix"
test -f "$prefix/lib/libspingnn2.so.1"
# DSPRHBM's system Python has no ensurepip; the first install qualified this path.
/usr/bin/python3 -m venv --without-pip "$prefix/venv"
/usr/bin/python3 -m pip --python "$prefix/venv/bin/python" install --no-index --find-links "$run/sources/wheels" \
  -r "$control/spingnn_runtime.txt" setuptools wheel > evidence/runtime-pip.log 2>&1
/usr/bin/python3 -m pip --python "$prefix/venv/bin/python" install --no-index --no-deps --no-build-isolation \
  --report "$run/evidence/runtime-pip-install.json" "$src[ase]" >> evidence/runtime-pip.log 2>&1
/usr/bin/python3 -m pip --python "$prefix/venv/bin/python" freeze > evidence/runtime-pip-freeze.txt
/usr/bin/python3 "$src/lammps_spingnn2/apply.py" --upstream "$run/sources/lammps-upstream" \
  --destination "$run/sources/lammps-overlay" > evidence/overlay-identity.txt
lammps_opts=(-DKokkos_ENABLE_CUDA=OFF -DCMAKE_CXX_COMPILER="$CXX" -DPKG_OPENMP=ON)
if [[ $SAI_SPINGNN_BACKEND == cuda ]]; then
  case "$SAI_SPINGNN_CUDA_ARCH" in
    70) kokkos_arch=VOLTA70 ;;
    80) kokkos_arch=AMPERE80 ;;
    *) echo 'Unqualified CUDA architecture' >&2; exit 1 ;;
  esac
  lammps_opts=(-DKokkos_ENABLE_CUDA=ON "-DKokkos_ARCH_$kokkos_arch=ON" -DKokkos_ENABLE_CUDA_LAMBDA=ON
    -DCMAKE_CXX_COMPILER="$run/sources/lammps-overlay/lib/kokkos/bin/nvcc_wrapper"
    -DCUDAToolkit_ROOT="$CUDA_HOME" -DPKG_OPENMP=OFF
    -DCMAKE_USER_MAKE_RULES_OVERRIDE_CXX="$src/lammps_spingnn2/cmake/cuda-cxx20.cmake")
fi
"$SPINGNN_CMAKE" -S "$run/sources/lammps-overlay/cmake" -B "$run/build/lammps" \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_MPI=ON -DBUILD_OMP=ON \
  -DPKG_SPIN=ON -DPKG_KOKKOS=ON -DKokkos_ENABLE_OPENMP=ON \
  -DCMAKE_CXX_STANDARD=20 -DLAMMPS_MACHINE=spingnn2 \
  -DCMAKE_PREFIX_PATH="$prefix" -DMPI_CXX_COMPILER="$MPI_HOME/bin/mpicxx" \
  -DCMAKE_C_COMPILER="$CC" -DCMAKE_INSTALL_PREFIX="$prefix" -DCMAKE_INSTALL_LIBDIR=lib \
  -DCMAKE_INSTALL_RPATH="\$ORIGIN/../lib;$SPINGNN_GCC/lib64;$MPI_HOME/lib;${CUDA_HOME:-/nonexistent}/lib64" \
  -DBUILD_SHARED_LIBS=OFF -DWITH_PNG=OFF -DWITH_JPEG=OFF -DWITH_FFMPEG=OFF \
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON "${lammps_opts[@]}"
"$SPINGNN_CMAKE" --build "$run/build/lammps" --target lmp --parallel 4
"$SPINGNN_CMAKE" --install "$run/build/lammps" --component spingnn2_runtime
"$SPINGNN_EXPORT_PYTHON" -m venv --system-site-packages --without-pip "$run/export-env"
/usr/bin/python3 -m pip --python "$run/export-env/bin/python" install --no-index --find-links "$run/sources/wheels" \
  --no-deps --no-build-isolation "$run/sources/T-e3nn-75334027fffc38f2268db8ab1eed281a18ffdbf2" torch-runstats==0.2.0 \
  > evidence/export-pip.log 2>&1
# pytest's own dependencies are needed by the imported upstream test harness.
/usr/bin/python3 -m pip --python "$run/export-env/bin/python" install --no-index --find-links "$run/sources/wheels" pytest >> evidence/export-pip.log 2>&1
/usr/bin/python3 -m pip --python "$run/export-env/bin/python" freeze > evidence/export-pip-freeze.txt
"$run/export-env/bin/python" "$control/spingnn_fixture.py" "$run" > evidence/fixture-export.log 2>&1
# Carry the exact sources/overlay, build recipe and notices with each delivery.
mkdir -p "$prefix/share/source" "$prefix/share/doc/spingnn2"
cp "$run"/input/spingnn-dependencies/*.tar.gz "$control/spingnn_runtime.txt" "$control/spingnn_build.sh" \
  "$control/spingnn_site.json" "$control"/spingnn_sm70.* \
  "$run/evidence/source-adaptation.json" "$prefix/share/source/"
tar --exclude=.git -czf "$prefix/share/source/lammps-spingnn2-corresponding-source.tar.gz" \
  -C "$run/sources/lammps-overlay" .
cp "$run/sources/lammps-overlay/LICENSE" "$prefix/share/doc/spingnn2/LAMMPS-LICENSE"
cp -a "$src/LICENSES" "$src/LICENSES.md" "$prefix/share/doc/spingnn2/"
/usr/bin/python3 "$control/spingnn_package.py" "$run" "$run/input/delivery.json"
