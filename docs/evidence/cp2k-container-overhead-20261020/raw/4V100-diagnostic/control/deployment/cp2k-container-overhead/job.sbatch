#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=01:30:00
#SBATCH --export=ALL
set -eo pipefail
run=${1:?frozen experiment directory}
export PATH=/usr/bin:/bin LD_LIBRARY_PATH= LD_PRELOAD= PYTHONDONTWRITEBYTECODE=1
export TMPDIR=/home/stardust/sai-hpc-software/runtime/co-${SLURM_JOB_ID:?}
export CUDA_CACHE_PATH=$TMPDIR/cuda-cache XDG_CACHE_HOME=$TMPDIR/cache
export APPTAINER_TMPDIR=$TMPDIR APPTAINER_CACHEDIR=$TMPDIR/apptainer-cache
mkdir -p "$TMPDIR" "$CUDA_CACHE_PATH" "$XDG_CACHE_HOME" "$APPTAINER_CACHEDIR"
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/apps /opt/modules/modulefiles/devtools
module load cp2k/2026.2-auto apptainer/1.4.4
if [[ $SLURM_JOB_PARTITION == DSPRHBM ]]; then
    export MAP_OPT=ppr:1:node:pe=2 OMP_NUM_THREADS=2
else
    source "/opt/sai_config/mps_mapping.d/$SLURM_JOB_PARTITION.bash"
    export OMP_NUM_THREADS=$CORES_PER_GPU
fi
set -u
export OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 OMP_DYNAMIC=FALSE
export OMP_PLACES=cores OMP_PROC_BIND=close SLURM_EXPORT_ENV=ALL
export PRTE_MCA_prte_tmpdir_base=$TMPDIR OMPI_MCA_orte_tmpdir_base=$TMPDIR
export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher
cd "$run"
id > results/account.txt
module -t list > results/modules.txt 2>&1
scontrol show job -o "$SLURM_JOB_ID" > results/allocation.txt
hostname > results/hostname.txt
lscpu -p=CPU,CORE,SOCKET > results/topology.txt
apptainer version > results/apptainer-version.txt
mpirun --version > results/mpi-version.txt
if [[ $SLURM_JOB_PARTITION != DSPRHBM ]]; then nvidia-smi > results/gpu.txt; fi
python3 "$run/control/deployment/cp2k-container-overhead/run.py" execute "$run"
