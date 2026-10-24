#!/bin/bash
#SBATCH --job-name=cp2k
#SBATCH --partition=DSPRHBM
#SBATCH --nodes=1
#SBATCH --ntasks=2
#SBATCH --cpus-per-task=2
#SBATCH --qos=rush-cpu

export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK
export OMP_PLACES=cores
export OMP_PROC_BIND=close

# Short home path for SIF scratch and Unix sockets.
export TMPDIR="$HOME/.sai-sif-$SLURM_JOB_ID"
mkdir -p "$TMPDIR"
module load cp2k/master-beta-later

mpirun -np $SLURM_NTASKS --map-by slot:pe=$OMP_NUM_THREADS cp2k.psmp -i input.inp -o output.out
exit
