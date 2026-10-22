#!/bin/bash
#SBATCH --job-name=fuse2fs-installed-bench
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=8G
#SBATCH --time=00:12:00
set -euo pipefail
base=/home/stardust/sai-hpc-software/diagnostics/fuse2fs-installed-benchmark-20261022
export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" PYTHONDONTWRITEBYTECODE=1
export TMPDIR="$base/runtime" APPTAINER_TMPDIR="$base/runtime" APPTAINER_CACHEDIR="$base/cache"
mkdir -p "$TMPDIR" "$APPTAINER_CACHEDIR"
/usr/bin/python3 -B "$base/input/run-fuse2fs-benchmark.py" "$base"
