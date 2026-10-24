#!/bin/bash
#SBATCH --job-name=abacus
#SBATCH --partition=16V100
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --qos=flood-1o2gpu

source /opt/sai/runtime/mps_mapping.d/${SLURM_JOB_PARTITION}.bash

# SIF scratch stays with the case under /home or /org.
export TMPDIR="$PWD/.sai-sif-$SLURM_JOB_ID"
mkdir -p "$TMPDIR"
module load abacus/develop-beta-later

mpirun -np $SLURM_NTASKS --map-by $MAP_OPT abacus > abacus.out
exit
