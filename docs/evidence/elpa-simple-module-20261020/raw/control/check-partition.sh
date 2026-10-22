#!/usr/bin/env bash
# Check the candidate selector against physical or container-installed ELPA.
set -eo pipefail
package=${1:?candidate module and smoke source}
output=${2:?writable result directory}
partition=${3:?partition}
export LD_PRELOAD=
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1
export OMPI_CC=/usr/bin/gcc OMPI_CXX=/usr/bin/g++ OMPI_FC=/usr/bin/gfortran
export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher
mkdir -p "$output/modulefiles/elpa"
name=2026.02.001-2603-gnu-auto
cp "$package/$name" "$output/modulefiles/elpa/$name"
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/devtools
module use "$output/modulefiles"
export SLURM_JOB_PARTITION=$partition
module load "elpa/$name"
set -u
case "$partition" in
  16V100|8V100V0) isa=avx2; backend=nvidia; gpu=1 ;;
  4V100) isa=avx512; backend=nvidia; gpu=1 ;;
  DSPRHBM) isa=avx512; backend=cpu; gpu=0 ;;
  *) exit 2 ;;
esac
prefix=/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-$isa/$backend
[[ "$ELPA_ROOT" == "$prefix" ]]
[[ "$MPI_HOME" == /opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-$isa ]]
[[ "$OPENBLAS_ROOT" == /opt/devtools/saiblas/2603-gnu-$isa ]]
cd "$output"
lscpu > cpu.txt
printf '%s\n' "${_LMFILES_:?}" | tr ':' '\n' | grep -Fx "$output/modulefiles/elpa/$name" > modulefile.txt
set +u
module -t list > modules.txt 2>&1
set -u
printf 'partition=%s\nisa=%s\nbackend=%s\nprefix=%s\n' "$partition" "$isa" "$backend" "$prefix" > selection.txt
ldd "$prefix/lib/libelpa_openmp.so" > ldd.txt
! grep -Eq 'not found|/opt/mellanox/hcoll' ldd.txt
if [[ "$backend" == cpu ]]; then ! grep -Eq 'lib(cuda|cublas|cusolver)' ldd.txt; fi
mpicc -std=c11 -O2 "$package/smoke.c" $(pkg-config --cflags --libs elpa_openmp) -lm -o smoke
ldd ./smoke > consumer-ldd.txt
grep -F "$prefix/lib/libelpa_openmp.so.19" consumer-ldd.txt
timeout 180 mpiexec --bind-to none -n 2 env LD_DEBUG=libs "LD_DEBUG_OUTPUT=$output/loader" \
  ./smoke "$gpu" 2 1 1 > numeric.log 2>&1 || { cat numeric.log; exit 1; }
[[ $(grep -c '^ELPA_NUMERIC PASS' numeric.log) == 2 ]]
# Fortran modules must remain discoverable for downstream compilation.
cat > fortran-smoke.f90 <<'F90'
program check_fortran_module
  use mpi
  use elpa
  implicit none
  integer :: error
  call MPI_Init(error)
  error = elpa_init(20221109)
  if (error /= ELPA_OK) stop 1
  call elpa_uninit(error)
  if (error /= ELPA_OK) stop 2
  call MPI_Finalize(error)
  print *, 'ELPA_FORTRAN_CONSUMER_PASS'
end program
F90
mpifort $(pkg-config --variable=fcflags elpa_openmp) fortran-smoke.f90 \
  $(pkg-config --libs elpa_openmp) -o fortran-smoke
timeout 60 mpiexec --bind-to none -n 1 ./fortran-smoke > fortran-smoke.log 2>&1
grep -q ELPA_FORTRAN_CONSUMER_PASS fortran-smoke.log
mpicc -shared -fPIC -O2 "$package/comm-trace.c" -ldl -o comm-trace.so
for mode in $(seq 0 "$gpu"); do
  timeout 300 mpiexec --bind-to none -n 2 \
    env ELPA_OBJECT_REPEATS=32 "LD_PRELOAD=$output/comm-trace.so" \
    ./smoke "$mode" 2 0 > "lifecycle-g$mode.log" 2>&1
  python3 "$package/verify-lifecycle.py" "lifecycle-g$mode.log" --ranks 2 --gpu "$mode" --objects 32
done
sha256sum "$output/modulefiles/elpa/$name" > module.sha256
# Unload must use ELPA_ROOT even after the partition variable changes.
assert_unloaded() {
  [[ -z ${ELPA_ROOT:-} && -z ${SAI_ELPA_2603_ISA:-} && -z ${SAI_ELPA_2603_PARTITION:-} && -z ${SAI_ELPA_2603_BACKEND:-} ]]
  local key
  for key in PATH LD_LIBRARY_PATH LD_RUN_PATH LIBRARY_PATH PKG_CONFIG_PATH CMAKE_PREFIX_PATH CPATH; do
    [[ ":${!key:-}:" != *":$prefix:"* && ":${!key:-}:" != *":$prefix/"* ]]
  done
}
export SLURM_JOB_PARTITION=not-the-loaded-partition
set +u
module unload "elpa/$name"
set -u
assert_unloaded
# Exercise replacement of the currently deployed partition selector on all nodes.
cp "$package/previous-auto" "$output/modulefiles/elpa/$name"
export SLURM_JOB_PARTITION=$partition
set +u
module load "elpa/$name"
set -u
[[ "$ELPA_ROOT" == "$prefix" && "$SAI_ELPA_2603_PARTITION" == "$partition" ]]
cp "$package/$name" "$output/modulefiles/elpa/$name"
unset SLURM_JOB_PARTITION
set +u
module unload "elpa/$name"
set -u
assert_unloaded
printf 'ELPA_PREVIOUS_AUTO_UNLOAD_PASS\n' > previous-unload.txt
if [[ "$backend" == nvidia ]]; then
  # Exercise an existing shell across replacement of the old module in place.
  cp "$package/legacy-auto" "$output/modulefiles/elpa/$name"
  set +u
  module load "elpa/$name"
  set -u
  [[ "$ELPA_ROOT" == "$prefix" && -z ${SAI_ELPA_2603_PARTITION:-} ]]
  cp "$package/$name" "$output/modulefiles/elpa/$name"
  set +u
  module unload "elpa/$name"
  set -u
  assert_unloaded
  printf 'ELPA_LEGACY_AUTO_UNLOAD_PASS\n' > legacy-unload.txt
fi
# Login/unsupported partitions must still be rejected before loading ELPA.
set +u
if module load "elpa/$name" > missing-partition.log 2>&1; then exit 1; fi
export SLURM_JOB_PARTITION=unsupported
if module load "elpa/$name" > unsupported-partition.log 2>&1; then exit 1; fi
set -u
assert_unloaded
export SLURM_JOB_PARTITION=$partition
printf 'ELPA_PARTITION_MODULE_PASS %s %s %s\n'  "$partition" "$isa" "$backend" > passed.txt
cat passed.txt
