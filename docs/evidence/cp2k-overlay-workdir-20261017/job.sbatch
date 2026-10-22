#!/bin/bash
#SBATCH --job-name=sai-overlay-workdir-repro
#SBATCH --partition=DSPRHBM
#SBATCH --qos=rush-cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=4G
#SBATCH --time=20
#SBATCH --export=NIL
#SBATCH --output=/home/stardust/sai-hpc-software/diagnostics/cp2k-release-20261016/overlay-workdir-repro-20261017-b/slurm-%j.log
set -euo pipefail
export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD="" PYTHONDONTWRITEBYTECODE=1
umask 077
base=/home/stardust/sai-hpc-software/diagnostics/cp2k-release-20261016/overlay-workdir-repro-20261017-b
/usr/bin/python3 -B "$base/input/repro.py"
