#!/bin/bash
#SBATCH --job-name=LMP
#SBATCH --partition=4V100
#SBATCH --nodes=4
#SBATCH --ntasks=16         # Nodes * GPUs-per-node * Ranks-per-GPU
#SBATCH --gpus-per-node=4   # Specify the GPUs-per-node
#SBATCH --qos=rush-gpu      # Depending on your needs [Priority: rush-* > improper-* = huge-* > flood-* = ultimate-*]

# ⚠ DO NOT modify [CUDA-MPS] and [Rank-Map] settings unless you know what you are doing.
source /opt/sai/runtime/mps_mapping.d/${SLURM_JOB_PARTITION}.bash

export OMP_NUM_THREADS=2

# Below are executing commands
nvidia-smi dmon -s pucvmte -o T > nvdmon_job-$SLURM_JOB_ID.log &
module load lammps/29aug2024-nvhpc245gnu-ompi508-sm70
mpirun -np $SLURM_NTASKS --map-by $MAP_OPT lmp -k on g $SLURM_GPUS_ON_NODE -sf kk -pk kokkos cuda/aware on neigh full comm device -in input.lammps

# Must explicitly exit
exit
