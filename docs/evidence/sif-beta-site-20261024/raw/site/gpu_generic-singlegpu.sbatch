#!/bin/bash
#SBATCH --job-name=General
#SBATCH --partition=4V100
#SBATCH --nodes=1
#SBATCH --ntasks=1          # Nodes * GPUs-per-node * Ranks-per-GPU
#SBATCH --gpus-per-node=1   # Specify the GPUs-per-node
#SBATCH --qos=improper-gpu  # Depending on your needs [Priority: rush-* > improper-* = huge-* > flood-* = ultimate-*]

nvidia-smi dmon -s pucvmte -o T > nvdmon_job-$SLURM_JOB_ID.log &

# 这是通用的单卡串行任务模板，请自行填写
#module load xxx/xxx
