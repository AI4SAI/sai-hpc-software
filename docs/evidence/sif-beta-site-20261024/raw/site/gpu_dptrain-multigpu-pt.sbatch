#!/bin/bash
#SBATCH --job-name=MultiGPUTraining-PyTorch
#SBATCH --partition=4V100
#SBATCH --nodes=1
#SBATCH --ntasks=1          # Nodes * GPUs-per-node * Ranks-per-GPU
#SBATCH --gpus-per-node=4   # Specify the GPUs-per-node
#SBATCH --qos=rush-gpu      # Depending on your needs [Priority: rush-* > improper-* = huge-* > flood-* = ultimate-*]

# Below are executing commands
nvidia-smi dmon -s pucvmte -o T > nvdmon_job-$SLURM_JOB_ID.log &
source /opt/sai/envs/deepmd3.1.2.env
export DP_INTERFACE_PREC=low
# For PT backend parallelization (DDP), using `torchrun` and set `--ntasks-per-node=1` instead of `--ntasks=xx`
torchrun --nproc_per_node=$((SLURM_NTASKS*SLURM_GPUS_ON_NODE)) --no-python --rdzv_backend=c10d --rdzv_endpoint=localhost:0 dp --pt train input.json
