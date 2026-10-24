#!/bin/bash
#SBATCH --job-name=CP2K
#SBATCH --partition=D9654
#SBATCH --nodes=1           # Specify the number of Node(s)
#SBATCH --ntasks=16         # Specify the number of MPI Rank(s)
#SBATCH --cpus-per-task=4   # Specify the number of CPU Core(s) of each MPI Rank
#SBATCH --qos=huge-cpu      # Depending on your needs [Priority: rush-cpu > huge-cpu]

# ⚠ DO NOT modify [OMP] settings unless you know what you are doing.
export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK
export OMP_PLACES=cores
export OMP_PROC_BIND=close

# Below are executing commands
module load cp2k/2025.1-cpu-auto
mpirun -np $SLURM_NTASKS --map-by ppr:$((8/OMP_NUM_THREADS)):l3cache:pe=$OMP_NUM_THREADS -mca coll_hcoll_enable 0 cp2k.psmp -i input.inp -o output.log
