#!/bin/bash
#SBATCH --job-name=lammps
#SBATCH --partition=16V100
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --qos=flood-1o2gpu

source /opt/sai/runtime/mps_mapping.d/${SLURM_JOB_PARTITION}.bash

export OMP_NUM_THREADS=2

# SIF scratch stays with the case under /home or /org.
export TMPDIR="$PWD/.sai-sif-$SLURM_JOB_ID"
mkdir -p "$TMPDIR"
module load lammps/develop-beta-later

mpirun -np $SLURM_NTASKS --map-by $MAP_OPT lmp -pk gpu $SLURM_GPUS_ON_NODE split 1.0 -in in.lmp -log log.lammps
exit
