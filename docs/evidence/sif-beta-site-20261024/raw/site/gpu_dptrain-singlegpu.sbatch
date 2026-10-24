#!/bin/bash
#SBATCH --job-name=DP-Train
#SBATCH --partition=4V100
#SBATCH --nodes=1
#SBATCH --ntasks=1          # Nodes * GPUs-per-node * Ranks-per-GPU
#SBATCH --gpus-per-node=1   # Specify the GPUs-per-node
#SBATCH --qos=improper-gpu  # Depending on your needs [Priority: rush-* > improper-* = huge-* > flood-* = ultimate-*]

export OMP_NUM_THREADS=2

nvidia-smi dmon -s pucvmte -o T > nvdmon_job-$SLURM_JOB_ID.log &
source /opt/sai/envs/deepmd3.1.2.env
export DP_INTERFACE_PREC=low
dp train input.json
dp freeze -o graph.pb
dp compress -i graph.pb -o compressed.pb
