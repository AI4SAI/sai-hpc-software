#!/usr/bin/env bash
set -euo pipefail
isa=${1:?}
source /control/environment.sh "$isa"
mkdir -p /workspace/verify
cd /workspace/verify
so=$ELPA_ROOT/lib/libelpa_openmp.so
ldd "$so" | tee ldd.txt
! grep -q 'not found' ldd.txt
! grep -q '/opt/mellanox/hcoll' ldd.txt
readelf -d "$so" > dynamic.txt
! grep -q '/opt/mellanox/hcoll' dynamic.txt
elpa2_print_kernels_openmp > kernels.txt
grep -q ELPA_2STAGE_REAL_AVX2_BLOCK kernels.txt
if [[ "$isa" == avx2 ]]; then
  ! grep -q ELPA_2STAGE_REAL_AVX512 kernels.txt
else
  grep -q ELPA_2STAGE_REAL_AVX512_BLOCK kernels.txt
fi
/opt/devtools/nvidia/cuda-12.9.1/bin/cuobjdump --list-elf "$so" > gpu-kernels.txt
for sm in 70 75 80 86 89; do grep -q "sm_$sm.cubin" gpu-kernels.txt; done
# Compile a fresh consumer against the final SIF's headers and shared library.
"$MPI_HOME/bin/mpicc" -std=c11 -O2 /control/smoke.c $(pkg-config --cflags --libs elpa_openmp) -lm -o smoke
ldd ./smoke > consumer-ldd.txt
grep -F "$ELPA_ROOT/lib/libelpa_openmp.so.19" consumer-ldd.txt
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
"$MPI_HOME/bin/mpifort" $(pkg-config --variable=fcflags elpa_openmp) fortran-smoke.f90 \
  $(pkg-config --libs elpa_openmp) -o fortran-smoke
timeout 60 "$MPI_HOME/bin/mpiexec" --bind-to none -n 1 ./fortran-smoke > fortran-smoke.log 2>&1
grep -q ELPA_FORTRAN_CONSUMER_PASS fortran-smoke.log
for ranks in 1 2; do
  for gpu in 0 1; do
    for solver in 1 2; do
      for complex in 0 1; do
        log=r$ranks-g$gpu-s$solver-c$complex.log
        timeout 180 "$MPI_HOME/bin/mpiexec" --bind-to none -n "$ranks" \
          ./smoke "$gpu" "$solver" "$complex" > "$log" 2>&1 || { cat "$log"; exit 1; }
        [[ $(grep -c '^ELPA_NUMERIC PASS' "$log") == 2 ]]
        grep '^ELPA_NUMERIC PASS' "$log"
        if [[ "$solver" == 2 ]]; then
          log=r$ranks-g$gpu-s$solver-c$complex-nbc1.log
          timeout 180 "$MPI_HOME/bin/mpiexec" --bind-to none -n "$ranks" \
            ./smoke "$gpu" "$solver" "$complex" 1 > "$log" 2>&1 || { cat "$log"; exit 1; }
          [[ $(grep -c '^ELPA_NUMERIC PASS' "$log") == 2 ]]
          grep '^ELPA_NUMERIC PASS' "$log"
        fi
      done
    done
  done
done
printf 'ELPA_SIF_VERIFIED %s\n' "$isa" | tee passed.txt
