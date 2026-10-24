#!/bin/bash
# Internal evidence driver; the public run.sbatch files are executed unchanged.
#SBATCH --time=00:10:00
set -eo pipefail
root=$PWD
export SAI_SIF_TRACE_DIR="$root/trace"
scontrol show job -o "$SLURM_JOB_ID" > allocation.txt
printf '%s\n' "$SLURM_JOB_ID" > job.id
for app in cp2k abacus; do
    module purge
    cd "$root/$app"
    /usr/bin/time -f '%e' -o elapsed.txt bash -x run.sbatch > stdout.txt 2>&1
done
cd "$root"
