#!/usr/bin/env bash
# Source after site modules, before exposing the external ML prefix to CMake.
# FindMPI's prefix search precedes PATH/MPI_HOME hints. Do not let a Conda
# prefix choose wrappers, headers or libraries from a second MPI installation.
: "${MPI_HOME:?site MPI module required}" "${full_deps:?}" "${ml_root:?}"
[[ "$MPI_HOME" == /* && "$MPI_HOME" != "$ml_root" ]]
for wrapper in mpicc mpicxx mpifort mpiexec; do
  test -x "$MPI_HOME/bin/$wrapper"
done
for library in mpi mpi_mpifh mpi_usempi_ignore_tkr mpi_usempif08; do
  test -s "$MPI_HOME/lib/lib$library.so"
done
test -s "$MPI_HOME/include/mpi.h"
test -s "$MPI_HOME/include/mpif.h"
test -s "$MPI_HOME/lib/mpi.mod"

# Keep ML transitive packages (protobuf/absl/MKL) discoverable, but after
# explicitly selected site dependencies. This does not activate Conda.
export CMAKE_PREFIX_PATH="$full_deps:$MPI_HOME:${CMAKE_PREFIX_PATH:+$CMAKE_PREFIX_PATH:}$ml_root"
abacus_mpi_options=(
  "-DMPI_HOME:PATH=$MPI_HOME"
  "-DMPIEXEC_EXECUTABLE:FILEPATH=$MPI_HOME/bin/mpiexec"
  "-DMPI_C_COMPILER:FILEPATH=$MPI_HOME/bin/mpicc"
  "-DMPI_CXX_COMPILER:FILEPATH=$MPI_HOME/bin/mpicxx"
  "-DMPI_Fortran_COMPILER:FILEPATH=$MPI_HOME/bin/mpifort"
  "-DMPI_C_HEADER_DIR:PATH=$MPI_HOME/include"
  "-DMPI_CXX_HEADER_DIR:PATH=$MPI_HOME/include"
  "-DMPI_Fortran_F77_HEADER_DIR:PATH=$MPI_HOME/include"
  "-DMPI_Fortran_MODULE_DIR:PATH=$MPI_HOME/lib")
for library in mpi mpi_mpifh mpi_usempi_ignore_tkr mpi_usempif08; do
  abacus_mpi_options+=("-DMPI_${library}_LIBRARY:FILEPATH=$MPI_HOME/lib/lib$library.so")
done

abacus_check_site_mpi() {
  local cache=$1 option
  # Validate the cache before expensive compilation; do not fake *_WORKS or
  # bypass FindMPI's real C/C++/Fortran compile/link probes.
  for option in "${abacus_mpi_options[@]}"; do
    if ! grep -Fxq -- "${option#-D}" "$cache"; then
      echo "MPI discovery escaped the site module: ${option#-D}" >&2
      return 1
    fi
  done
  for option in C CXX Fortran; do
    if ! grep -Eq "^MPI_RESULT_${option}_test_mpi_.*:INTERNAL=TRUE$" "$cache"; then
      echo "Site MPI $option compile/link probe did not pass" >&2
      return 1
    fi
  done
  echo "SITE_MPI_CONFIG_VERIFIED $MPI_HOME"
}
