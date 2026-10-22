#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=02:00:00
#SBATCH --export=ALL
set -eo pipefail
run=${1:?frozen experiment}
export PATH=/usr/bin:/bin LD_LIBRARY_PATH= LD_PRELOAD= PYTHONDONTWRITEBYTECODE=1
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/devtools
module load apptainer/1.4.4 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto
source "/opt/sai_config/mps_mapping.d/${SLURM_JOB_PARTITION:?}.bash"
set -u
[[ "$SLURM_NTASKS" == 1 && "$SLURM_GPUS_ON_NODE" == 1 ]]
export OMP_NUM_THREADS=$CORES_PER_GPU OMP_PLACES=cores OMP_PROC_BIND=close
export TMPDIR=/home/stardust/sai-hpc-software/runtime/ct-${SLURM_JOB_ID:?}
export CUDA_CACHE_PATH=$TMPDIR/cuda-cache XDG_CACHE_HOME=$TMPDIR/cache
export PRTE_MCA_prte_tmpdir_base=$TMPDIR OMPI_MCA_orte_tmpdir_base=$TMPDIR
export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher SLURM_EXPORT_ENV=ALL
mkdir -p "$TMPDIR" "$CUDA_CACHE_PATH" "$XDG_CACHE_HOME"
cd "$run"
scontrol show job -o "$SLURM_JOB_ID" > results/allocation.txt
hostname > results/hostname.txt
nvidia-smi -i "$CUDA_VISIBLE_DEVICES" > results/gpu.txt
module -t list > results/modules.txt 2>&1
python3 "$run/control/deployment/cp2k-diagonalization/run.py" execute "$run"
