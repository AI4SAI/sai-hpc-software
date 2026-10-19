#!/usr/bin/env bash
# Check each final candidate SIF on its actual deployment partitions.
set -euo pipefail
package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
avx2=${1:?AVX2 run}; avx512=${2:?AVX512 run}; cpu=${3:?CPU run}
root=/home/stardust/sai-hpc-software
stamp=$(date +%Y%m%d-%H%M%S)
results=$root/runtime-tests/elpa-commfix-$stamp
mkdir -p "$results"
for partition in 16V100 4V100 8V100V0 DSPRHBM; do
  case "$partition" in
    16V100|8V100V0) build=$avx2; target=avx2 ;;
    4V100) build=$avx512; target=avx512 ;;
    DSPRHBM) build=$cpu; target=dsprhbm ;;
  esac
  [[ "$build" == "$root/runs/elpa-"* && -f "$build/job.id" ]]
  run=$root/runs/elpa-commfix-check-$stamp-$partition
  mkdir -p "$run"/{control,work,runtime,cache}
  cp "$build/control/"{container.sh,environment.sh,verify.sh,smoke.c,comm-trace.c,verify-lifecycle.py} "$run/control/"
  cp "$package/check.sbatch" "$run/control/"
  cp "$package/2026.02.001-2603-gnu-auto" "$run/control/"
  cp "$package/../elpa-2026.02.001-cpu/check-partition.sh" "$run/control/"
  cp "$package/../elpa-2026.02.001/2026.02.001-2603-gnu-auto" "$run/control/legacy-auto"
  ln -s "$build/elpa-$target.sif" "$run/elpa-$target.sif"
  ln -s "$run" "$results/$partition"
  (cd "$run/control" && sha256sum *) > "$run/controls.sha256"
  resources=(--qos=flood-1o2gpu --gpus-per-node=1)
  if [[ "$partition" == DSPRHBM ]]; then resources=(--qos=rush-cpu --cpus-per-task=4); fi
  job=$(sbatch --parsable --partition="$partition" "${resources[@]}" \
    --dependency="afterok:$(cat "$build/job.id")" --job-name="elpa-fix-check-$partition" \
    --output="$run/slurm-%j.log" "$run/control/check.sbatch" "$run" "$target" "$partition")
  printf '%s\n' "$job" > "$run/job.id"
  printf '%s\t%s\t%s\n' "$partition" "$job" "$run" | tee -a "$results/jobs.tsv"
done
printf 'ELPA repair checks: %s\n' "$results"
