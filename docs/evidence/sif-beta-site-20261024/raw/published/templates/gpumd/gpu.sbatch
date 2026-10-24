#!/bin/bash
#SBATCH --job-name=gpumd
#SBATCH --partition=16V100
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --qos=flood-1o2gpu

# Short home path for SIF scratch and Unix sockets.
export TMPDIR="$HOME/.sai-sif-$SLURM_JOB_ID"
mkdir -p "$TMPDIR"
module load gpumd/master-beta-later

gpumd > gpumd.out
exit
