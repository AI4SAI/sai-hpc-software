#!/bin/bash
#SBATCH --job-name=MultiGPUTraining-TensorFlow
#SBATCH --partition=4V100
#SBATCH --nodes=1
#SBATCH --ntasks=4          # Nodes * GPUs-per-node * Ranks-per-GPU
#SBATCH --gpus-per-node=4   # Specify the GPUs-per-node
#SBATCH --qos=rush-gpu      # Depending on your needs [Priority: rush-* > improper-* = huge-* > flood-* = ultimate-*]

# ⚠ DO NOT modify [CUDA-MPS] and [Rank-Map] settings unless you know what you are doing.
source /opt/sai/runtime/mps_mapping.d/${SLURM_JOB_PARTITION}.bash

export OMP_NUM_THREADS=2

# Below are executing commands
nvidia-smi dmon -s pucvmte -o T > nvdmon_job-$SLURM_JOB_ID.log &
source /opt/sai/envs/deepmd3.1.2.env
export DP_INTERFACE_PREC=low
mpirun -np $SLURM_NTASKS --map-by $MAP_OPT dp train input.json
dp freeze -o graph.pb
dp compress -i graph.pb -o compressed.pb

# Must explicitly exit
exit
