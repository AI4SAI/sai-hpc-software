#!/bin/bash
#SBATCH --time=00:05:00
set -eo pipefail
source /etc/profile.d/lmod.sh
control="$PWD/control"
export TMPDIR="/org/org-open-sai/proj-sai-hpc-software/.dt-$SLURM_JOB_ID"
mkdir -m 700 "$TMPDIR"
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export DP_INTRA_OP_PARALLELISM_THREADS=1 DP_INTER_OP_PARALLELISM_THREADS=1
for installation in sif release site; do
    module purge
    mkdir -p "$control/../frameworks/$installation"
    cd "$control/../frameworks/$installation"
    cp "$control/frameworks.py" .
    case "$installation" in
        sif) module load deepmd-kit/master-beta-later; command=(deepmd-python) ;;
        release) module load lammps/30Sep2026-deepmd320-auto; command=(python) ;;
        site) module load deepmd-kit/3.2.0; command=("$DEEPMD_PYTHON") ;;
    esac
    "${command[@]}" frameworks.py > frameworks.log 2>&1
done
