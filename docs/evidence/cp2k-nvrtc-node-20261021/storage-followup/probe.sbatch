#!/bin/bash
#SBATCH --time=00:05:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
set -e
base=${1:?}
export TMPDIR=$base/runtime-$SLURM_JOB_ID
mkdir -p "$TMPDIR"
python3 -B "$base/storage_probe.py" > "$base/job-$SLURM_JOB_ID.json"
