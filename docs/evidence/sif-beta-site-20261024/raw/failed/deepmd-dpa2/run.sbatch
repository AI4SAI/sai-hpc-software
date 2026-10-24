#!/bin/bash
#SBATCH --job-name=deepmd-kit
#SBATCH --partition=16V100
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=2
#SBATCH --qos=flood-1o2gpu

export OMP_NUM_THREADS=2

# Short home path for SIF scratch and Unix sockets.
export TMPDIR="$HOME/.sai-sif-$SLURM_JOB_ID"
mkdir -p "$TMPDIR"
module load deepmd-kit/master-beta-later

# torchrun inside the SIF; one worker per GPU on this node.
deepmd-python -m torch.distributed.run --standalone \
    --nproc_per_node=$SLURM_GPUS_ON_NODE -m deepmd --pt train input.json
exit
