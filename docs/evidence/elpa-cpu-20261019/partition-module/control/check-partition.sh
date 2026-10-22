#!/usr/bin/env bash
# Run natively for installed GPU prefixes, inside the candidate SIF for CPU.
set -eo pipefail
package=${1:?candidate module and smoke source}
output=${2:?writable result directory}
partition=${3:?partition}
export LD_PRELOAD=
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1
export OMPI_CC=/usr/bin/gcc OMPI_CXX=/usr/bin/g++ OMPI_FC=/usr/bin/gfortran
export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher
mkdir -p "$output/modulefiles/elpa"
name=2026.02.001-2603-gnu-partition
cp "$package/$name" "$output/modulefiles/elpa/$name"
source /etc/profile.d/lmod.sh
module purge
module use "$output/modulefiles" /opt/modules/modulefiles/devtools
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
[[ "$ELPA_ROOT" == "$prefix" && "$SAI_ELPA_2603_BACKEND" == "$backend" ]]
[[ "$MPI_HOME" == /opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-$isa ]]
[[ "$OPENBLAS_ROOT" == /opt/devtools/saiblas/2603-gnu-$isa ]]
cd "$output"
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
sha256sum "$output/modulefiles/elpa/$name" > module.sha256
# The unload must use the saved identity even if a caller changes its job env.
export SLURM_JOB_PARTITION=not-the-loaded-partition
set +u
module unload "elpa/$name"
set -u
[[ -z ${ELPA_ROOT:-} && -z ${SAI_ELPA_2603_PARTITION:-} && -z ${SAI_ELPA_2603_BACKEND:-} ]]
printf 'ELPA_PARTITION_MODULE_PASS %s %s %s\n' "$partition" "$isa" "$backend" > passed.txt
cat passed.txt
