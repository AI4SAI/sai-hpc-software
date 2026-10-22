#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=2
#SBATCH --time=00:05:00
#SBATCH --export=ALL
set -euo pipefail
package=${1:?frozen controls}
output=${2:?result directory}
cpu_run=${3:?CPU build run}
partition=${SLURM_JOB_PARTITION:?}
export PATH=/usr/bin:/bin LD_LIBRARY_PATH= LD_PRELOAD=
export TMPDIR=/home/stardust/sai-hpc-software/runtime/em-${SLURM_JOB_ID:?}
export CUDA_CACHE_PATH=$TMPDIR/cuda-cache XDG_CACHE_HOME=$TMPDIR/cache
export PRTE_MCA_prte_tmpdir_base=$TMPDIR SLURM_EXPORT_ENV=ALL
mkdir -p "$TMPDIR" "$CUDA_CACHE_PATH" "$XDG_CACHE_HOME"
hostname > "$output/hostname.txt"
scontrol show job -o "$SLURM_JOB_ID" > "$output/allocation.txt"
if [[ "$partition" == DSPRHBM ]]; then
  # Freeze controls separately from the accepted build recipe.
  check=auto-module-$SLURM_JOB_ID
  mkdir -p "$cpu_run/work/$check/control"
  cp "$package"/{check-partition.sh,2026.02.001-2603-gnu-auto,smoke.c} "$cpu_run/work/$check/control/"
  bash "$cpu_run/control/container.sh" verify "$cpu_run" dsprhbm \
    bash "/workspace/$check/control/check-partition.sh" "/workspace/$check/control" "/workspace/$check/results" DSPRHBM
  cp -a "$cpu_run/work/$check/results/." "$output/"
else
  set +u
  source "/opt/sai_config/mps_mapping.d/$partition.bash"
  set -u
  bash "$package/check-partition.sh" "$package" "$output" "$partition"
fi
