#!/bin/bash
#SBATCH --job-name=cp2k
#SBATCH --partition=16V100
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --qos=flood-1o2gpu

source /opt/sai/runtime/mps_mapping.d/${SLURM_JOB_PARTITION}.bash

export OMP_NUM_THREADS=$((CORES_PER_GPU/RANKS_PER_GPU))
export OMP_PLACES=cores
export OMP_PROC_BIND=close

# Short home path for SIF scratch and Unix sockets.
export TMPDIR="$HOME/.sai-sif-$SLURM_JOB_ID"
mkdir -p "$TMPDIR"
module load cp2k/master-beta-later

mpirun -np $SLURM_NTASKS --map-by $MAP_OPT cp2k.psmp -i input.inp -o output.out
exit
