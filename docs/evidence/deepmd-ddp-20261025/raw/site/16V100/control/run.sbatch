#!/bin/bash
# Internal validation driver, not a user template.
#SBATCH --time=00:15:00
set -eo pipefail
source /etc/profile.d/lmod.sh
root="$PWD"
cd "$root/$SLURM_JOB_PARTITION"
mkdir -p trace
scontrol show job -o "$SLURM_JOB_ID" > allocation.txt
module purge
module use "$root/bundle/modulefiles"
module load deepmd-kit-sif/master-20261025-g558f56962775
module list > modules.txt 2>&1
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export DP_INTRA_OP_PARALLELISM_THREADS=1 DP_INTER_OP_PARALLELISM_THREADS=1
export SAI_SIF_TRACE_DIR="$PWD/trace"
deepmd-python control/check.py > driver.log 2>&1
