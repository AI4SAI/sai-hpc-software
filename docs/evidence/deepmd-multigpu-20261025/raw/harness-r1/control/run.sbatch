#!/bin/bash
# Internal acceptance driver, not a public user template.
#SBATCH --time=00:20:00
set -eo pipefail
source /etc/profile.d/lmod.sh
control="$PWD/control"
root="$PWD/$SLURM_JOB_PARTITION"
mkdir -p "$root"
scontrol show job -o "$SLURM_JOB_ID" > "$root/allocation.txt"
export TMPDIR="/org/org-open-sai/proj-sai-hpc-software/.dt-$SLURM_JOB_ID"
mkdir -m 700 "$TMPDIR"
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export DP_INTRA_OP_PARALLELISM_THREADS=1 DP_INTER_OP_PARALLELISM_THREADS=1
for installation in sif release site; do
    module purge
    mkdir -p "$root/$installation"
    cd "$root/$installation"
    case "$installation" in
        sif)
            module load deepmd-kit/master-beta-later
            command=(deepmd-python)
            export SAI_SIF_TRACE_DIR="$PWD/trace"
            ;;
        release)
            module load lammps/30Sep2026-deepmd320-auto
            command=(python)
            ;;
        site)
            module load deepmd-kit/3.2.0
            command=("$DEEPMD_PYTHON")
            ;;
    esac
    module list > modules.txt 2>&1
    "${command[@]}" "$control/check.py" "$installation" > driver.log 2>&1 || printf '%s\n' "$?" > driver-error.txt
done
