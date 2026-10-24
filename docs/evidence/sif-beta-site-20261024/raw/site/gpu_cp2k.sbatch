#!/bin/bash
#SBATCH --job-name=CP2K
#SBATCH --partition=4V100
#SBATCH --nodes=8
#SBATCH --ntasks=64         # Nodes * GPUs-per-node * Ranks-per-GPU
#SBATCH --gpus-per-node=4   # Specify the GPUs-per-node
#SBATCH --qos=huge-gpu      # Depending on your needs [Priority: rush-* > improper-* = huge-* > flood-* = ultimate-*]

# ⚠ DO NOT modify [CUDA-MPS] and [Rank-Map] settings unless you know what you are doing.
source /opt/sai/runtime/mps_mapping.d/${SLURM_JOB_PARTITION}.bash

export OMP_NUM_THREADS=$((CORES_PER_GPU/RANKS_PER_GPU))
export OMP_PLACES=cores
export OMP_PROC_BIND=close

# Below are executing commands
nvidia-smi dmon -s pucvmte -o T > nvdmon_job-$SLURM_JOB_ID.log &
module load cp2k/2025.1-cuda12.4-sm70-auto
mpirun -np $SLURM_NTASKS --map-by $MAP_OPT cp2k.psmp -i input.inp -o output.log

# Must explicitly exit
exit
