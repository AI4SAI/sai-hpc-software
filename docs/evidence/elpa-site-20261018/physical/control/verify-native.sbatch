#!/usr/bin/env bash
# Execute the installed library and public module directly, without a container.
#SBATCH --nodes=1
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=2
#SBATCH --time=00:15:00
#SBATCH --export=ALL
set -eo pipefail
control=${1:?frozen controls}
output=${2:?results directory}
work_root=${3:?home-backed work directory}
isa=${4:?expected ISA}
gpu_max=${5:?0 for CPU-only or 1 for CPU and GPU}
[[ -d ${HOME:?Slurm must preserve the executing account home for OpenMPI} ]]
export PATH=/usr/bin:/bin LD_LIBRARY_PATH= LD_PRELOAD=
export PYTHONDONTWRITEBYTECODE=1
export TMPDIR="$work_root/runtime/en-${SLURM_JOB_ID:?}"
export CUDA_CACHE_PATH="$TMPDIR/cuda-cache" XDG_CACHE_HOME="$TMPDIR/cache"
export PRTE_MCA_prte_tmpdir_base="$TMPDIR" OMPI_MCA_orte_tmpdir_base="$TMPDIR"
export SLURM_EXPORT_ENV=ALL
mkdir -p "$TMPDIR" "$CUDA_CACHE_PATH" "$XDG_CACHE_HOME"
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/devtools
module load elpa/2026.02.001-2603-gnu-auto
if [[ "$gpu_max" == 1 ]]; then
  source "/opt/sai_config/mps_mapping.d/${SLURM_JOB_PARTITION:?}.bash"
fi
set -u
[[ "$isa" == avx2 || "$isa" == avx512 ]]
[[ "$gpu_max" == 0 || "$gpu_max" == 1 ]]
[[ "$ELPA_ROOT" == /opt/devtools/elpa/elpa-2026.02.001-2603-gnu-$isa/nvidia ]]
[[ "$MPI_HOME" == /opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-$isa ]]
[[ "$OPENBLAS_ROOT" == /opt/devtools/saiblas/2603-gnu-$isa ]]
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1
export OMPI_CC=/usr/bin/gcc OMPI_CXX=/usr/bin/g++ OMPI_FC=/usr/bin/gfortran
export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher
cd "$output"
python3 "$control/install.py" --verify > installed-files.txt
hostname > hostname.txt
lscpu > cpu.txt
taskset -pc $$ > affinity.txt
scontrol show job -o "$SLURM_JOB_ID" > allocation.txt
set +u
module -t list > modules.txt 2>&1
set -u
printf 'ELPA_ROOT=%s\nMPI_HOME=%s\nOPENBLAS_ROOT=%s\nLD_LIBRARY_PATH=%s\nTMPDIR=%s\nCUDA_VISIBLE_DEVICES=%s\n' \
  "$ELPA_ROOT" "$MPI_HOME" "$OPENBLAS_ROOT" "$LD_LIBRARY_PATH" "$TMPDIR" "${CUDA_VISIBLE_DEVICES:-}" > environment.txt
printf '%s\n' "$SLURM_JOB_ID" "$SLURM_JOB_PARTITION" "$isa" "$gpu_max" > identity.txt
gcc --version > compiler.txt
mpifort --showme >> compiler.txt
mpiexec --version > mpi-version.txt
if [[ "$gpu_max" == 1 ]]; then nvidia-smi > gpu.txt; fi
ldd "$ELPA_ROOT/lib/libelpa_openmp.so" > ldd.txt
readelf -d "$ELPA_ROOT/lib/libelpa_openmp.so" > dynamic.txt
! grep -Eq 'not found|/opt/mellanox/hcoll' ldd.txt
! grep -q '/opt/mellanox/hcoll' dynamic.txt
elpa2_print_kernels_openmp > kernels.txt
grep -q ELPA_2STAGE_REAL_AVX2_BLOCK kernels.txt
if [[ "$isa" == avx2 ]]; then
  ! grep -q ELPA_2STAGE_REAL_AVX512 kernels.txt
else
  grep -q ELPA_2STAGE_REAL_AVX512_BLOCK kernels.txt
fi
pkg-config --modversion elpa_openmp > pkg-version.txt
[[ $(cat pkg-version.txt) == 2026.02.001 ]]
mpicc -std=c11 -O2 "$control/smoke.c" $(pkg-config --cflags --libs elpa_openmp) -lm -o smoke
ldd ./smoke > consumer-ldd.txt
grep -F "$ELPA_ROOT/lib/libelpa_openmp.so.19" consumer-ldd.txt
! grep -Eq 'not found|/opt/mellanox/hcoll' consumer-ldd.txt
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
for ranks in 1 2; do
  for gpu in $(seq 0 "$gpu_max"); do
    for solver in 1 2; do
      for complex in 0 1; do
        nbcs=(0)
        if [[ "$solver" == 2 ]]; then nbcs+=(1); fi
        for nbc in "${nbcs[@]}"; do
          log=r$ranks-g$gpu-s$solver-c$complex-nbc$nbc.log
          prefix=()
          # Capture loader initialization from both real MPI processes once.
          if [[ "$ranks:$gpu:$solver:$complex:$nbc" == "2:$gpu_max:2:1:1" ]]; then
            prefix=(env LD_DEBUG=libs "LD_DEBUG_OUTPUT=$output/loader")
          fi
          timeout 180 mpiexec --bind-to none -n "$ranks" "${prefix[@]}" \
            ./smoke "$gpu" "$solver" "$complex" "$nbc" > "$log" 2>&1 || { cat "$log"; exit 1; }
          [[ $(grep -c '^ELPA_NUMERIC PASS' "$log") == 2 ]]
          grep '^ELPA_NUMERIC PASS' "$log"
        done
      done
    done
  done
done
set +u
module unload elpa/2026.02.001-2603-gnu-auto
set -u
[[ -z ${ELPA_ROOT:-} && -z ${SAI_ELPA_2603_ISA:-} ]]
printf 'ELPA_PHYSICAL_VERIFIED %s %s\n' "$SLURM_JOB_PARTITION" "$isa" | tee passed.txt
