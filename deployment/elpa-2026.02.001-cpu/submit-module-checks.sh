#!/usr/bin/env bash
set -euo pipefail
package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
cpu_run=${1:?accepted CPU run directory}
[[ -f "$cpu_run/accepted.txt" ]]
root=/home/stardust/sai-hpc-software/runtime-tests/elpa-partition-$(date +%Y%m%d-%H%M%S)-$$
mkdir -p "$root/control"
cp "$package"/{check-partition.sh,check-partition.sbatch,2026.02.001-2603-gnu-partition} "$root/control/"
cp "$package/../elpa-2026.02.001/smoke.c" "$root/control/"
(cd "$root/control" && sha256sum *) > "$root/controls.sha256"
for partition in 16V100 4V100 8V100V0 DSPRHBM; do
  output=$root/$partition
  mkdir "$output"
  resources=(--qos=flood-1o2gpu --gpus-per-node=1)
  if [[ "$partition" == DSPRHBM ]]; then resources=(--qos=rush-cpu --cpus-per-task=2); fi
  job=$(sbatch --parsable --partition="$partition" "${resources[@]}" \
    --job-name="elpa-partition-$partition" --output="$output/slurm-%j.log" \
    "$root/control/check-partition.sbatch" "$root/control" "$output" "$cpu_run")
  printf '%s\n' "$job" > "$output/job.id"
  printf '%s\t%s\t%s\n' "$partition" "$job" "$output" | tee -a "$root/jobs.tsv"
done
printf 'Partition module results: %s\n' "$root"
