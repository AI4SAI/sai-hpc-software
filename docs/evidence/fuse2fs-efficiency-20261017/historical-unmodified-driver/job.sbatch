#!/bin/bash
#SBATCH --job-name=sai-fuse2fs-16v100-check
#SBATCH --partition=16V100
#SBATCH --qos=flood-1o2gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --time=20
#SBATCH --export=NIL
#SBATCH --output=/home/stardust/sai-hpc-software/diagnostics/cp2k-release-20261016/fuse2fs-efficiency-16v100-20261017-a/slurm-%j.log
set -euo pipefail
export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""
umask 077
bench=/home/stardust/sai-hpc-software/diagnostics/cp2k-release-20261016/fuse2fs-efficiency-16v100-20261017-a
export TMPDIR="$bench/runtime" APPTAINER_TMPDIR="$bench/runtime" APPTAINER_CACHEDIR="$bench/cache"
mkdir -p "$bench/runtime" "$bench/cache"
cd "$bench"
sha256sum --check driver.sha256
date --iso-8601=seconds
hostname
python3 -c 'import os; print("CPU affinity:", sorted(os.sched_getaffinity(0))); assert len(os.sched_getaffinity(0)) >= 8'
/usr/bin/python3 "$bench/input/run-fuse2fs-benchmark.py" "$bench"
/usr/bin/python3 "$bench/input/summarize-fuse2fs-benchmark.py" "$bench" > "$bench/summary.log"
date --iso-8601=seconds
