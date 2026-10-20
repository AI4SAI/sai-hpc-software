#!/usr/bin/env bash
#SBATCH --nodes=2
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=1
#SBATCH --gpus-per-node=1
#SBATCH --time=00:40:00
#SBATCH --export=ALL
set -eo pipefail
output=${1:?native result directory required}
work_root=${2:?writable home work directory required}
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export LD_LIBRARY_PATH="" LD_PRELOAD="" PYTHONPATH=""
unset PYTHONHOME SAI_MD_ALLOCATED_JOB SAI_MD_ALLOCATED_NODE
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
# Every runtime/cache directory is on home storage, including MPI sockets.
export TMPDIR="$work_root/runtime/ln-${SLURM_JOB_ID:?}"
export TMP="$TMPDIR" TEMP="$TMPDIR" XDG_RUNTIME_DIR="$TMPDIR"
export CUDA_CACHE_PATH="$output/cuda-cache" XDG_CACHE_HOME="$output/cache"
mkdir -p "$TMPDIR" "$CUDA_CACHE_PATH" "$XDG_CACHE_HOME"
chmod 700 "$TMPDIR"
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/devtools /opt/modules/modulefiles/apps
module load lammps/30Sep2026-deepmd320-auto
set -u
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export DP_INTRA_OP_PARALLELISM_THREADS=1 DP_INTER_OP_PARALLELISM_THREADS=1
export TF_NUM_INTRAOP_THREADS=1 TF_NUM_INTEROP_THREADS=1
export SLURM_EXPORT_ENV=ALL
export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher
module -t list > "$output/modules.txt" 2>&1
scontrol show job -o "$SLURM_JOB_ID" > "$output/allocation.txt"
scontrol show hostnames "$SLURM_JOB_NODELIST" > "$output/nodes.txt"
command -v lmp > "$output/binary.txt"
command -v mpirun > "$output/mpirun.txt"
mpirun --version > "$output/mpi-version.txt"
/usr/bin/python3 -B "$output/control/native_checks.py" run "$output"
module unload lammps/30Sep2026-deepmd320-auto
[[ -z "${SAI_LAMMPS_PREFIX:-}" && -z "${SAI_LAMMPS_NATIVE_PARTITION:-}" ]]
printf 'LAMMPS_NATIVE_MODULE_UNLOADED\n' > "$output/module-unloaded.txt"
