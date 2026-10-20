#!/usr/bin/env bash
# Run against the actual public module and physical libraries, without Apptainer.
#SBATCH --nodes=1
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=2
#SBATCH --time=00:10:00
#SBATCH --export=ALL
set -euo pipefail
control=${1:?frozen controls}; output=${2:?results}
partition=${SLURM_JOB_PARTITION:?}
export PATH=/usr/bin:/bin LD_LIBRARY_PATH= LD_PRELOAD=
export TMPDIR=$output/runtime CUDA_CACHE_PATH=$output/runtime/cuda-cache XDG_CACHE_HOME=$output/runtime/cache
export PRTE_MCA_prte_tmpdir_base=$TMPDIR OMPI_MCA_orte_tmpdir_base=$TMPDIR SLURM_EXPORT_ENV=ALL
export PYTHONDONTWRITEBYTECODE=1
mkdir -p "$TMPDIR" "$CUDA_CACHE_PATH" "$XDG_CACHE_HOME"
hostname > "$output/hostname.txt"
scontrol show job -o "$SLURM_JOB_ID" > "$output/allocation.txt"
if [[ "$partition" != DSPRHBM ]]; then
  set +u
  source "/opt/sai_config/mps_mapping.d/$partition.bash"
  set -u
fi
python3 "$control/native-checks.py" --inventory "$control" > "$output/inventory-before.json"
bash "$control/check-partition.sh" "$control" "$output" "$partition" public
# Exercise CP2K's GCC 14 type-bound Fortran API against the GCC 13-built library.
set +u
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/devtools
module load elpa/2026.02.001-2603-gnu-auto gcc/14.3.0
set -u
export OMPI_CC=/opt/devtools/gcc/14.3.0/bin/gcc OMPI_CXX=/opt/devtools/gcc/14.3.0/bin/g++
export OMPI_FC=/opt/devtools/gcc/14.3.0/bin/gfortran
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher
"$OMPI_FC" -dumpfullversion > "$output/gcc14-version.txt"
[[ $(cat "$output/gcc14-version.txt") == 14.3.0 ]]
"$MPI_HOME/bin/mpifort" -fopenmp $(pkg-config --variable=fcflags elpa_openmp) \
  "$control/cp2k_elpa_abi.f90" $(pkg-config --libs elpa_openmp) -o "$output/gcc14-probe"
ldd "$output/gcc14-probe" > "$output/gcc14-ldd.txt"
timeout 60 "$MPI_HOME/bin/mpiexec" --bind-to none -n 1 "$output/gcc14-probe" > "$output/gcc14.log" 2>&1
grep -q CP2K_SITE_ELPA_GCC14_PASS "$output/gcc14.log"
python3 "$control/native-checks.py" --inventory "$control" > "$output/inventory-after.json"
printf 'ELPA_BACKTRANSFORM_NATIVE_PASS %s\n' "$partition" > "$output/accepted.txt"
