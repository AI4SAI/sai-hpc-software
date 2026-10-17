#!/bin/bash
#SBATCH --job-name=sai-fuse2fs-ro-recheck
#SBATCH --partition=DSPRHBM
#SBATCH --qos=rush-cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=28G
#SBATCH --time=10
#SBATCH --export=NIL
#SBATCH --output=/home/stardust/sai-hpc-software/diagnostics/cp2k-release-20261016/fuse2fs-readonly-20261017-b/slurm-%j.log
set -euo pipefail
export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD="" PYTHONDONTWRITEBYTECODE=1
umask 077
bench=/home/stardust/sai-hpc-software/diagnostics/cp2k-release-20261016/fuse2fs-readonly-20261017-b
export TMPDIR="$bench/runtime" APPTAINER_TMPDIR="$bench/runtime" APPTAINER_CACHEDIR="$bench/cache"
cd "$bench"
sha256sum --check input.sha256
printf '%s  %s\n' 46c1a8c8f42ab2419ea88072e41d89903c453d93f52485b5baddea5ca238bb10 driver/fuse2fs | sha256sum --check
date --iso-8601=seconds
hostname
python3 -B "$bench/input/probe.py" "$bench"
date --iso-8601=seconds
