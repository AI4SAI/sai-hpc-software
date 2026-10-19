#!/usr/bin/env bash
# Freeze and test the simplified selector without changing the public module.
set -euo pipefail
umask 022
package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
mode=${1:-candidate}
[[ "$mode" == candidate || "$mode" == public ]]
root=/home/stardust/sai-hpc-software/runtime-tests/elpa-simple-$(date +%Y%m%d-%H%M%S)-$$
mkdir -p "$root/control"
cp "$package"/{check-partition.sh,check-module.sbatch,2026.02.001-2603-gnu-auto,install.py,release.json,*-manifest.json} "$root/control/"
cp "$package/../elpa-2026.02.001/"{smoke.c,comm-trace.c,verify-lifecycle.py} "$root/control/"
cp "$package/../elpa-2026.02.001/2026.02.001-2603-gnu-auto" "$root/control/legacy-auto"
cp "$package/../elpa-2026.02.001-cpu/2026.02.001-2603-gnu-auto" "$root/control/previous-auto"
(cd "$root/control" && sha256sum *) > "$root/controls.sha256"
for partition in 16V100 4V100 8V100V0 DSPRHBM; do
  output=$root/$partition
  mkdir "$output"
  resources=(--qos=flood-1o2gpu --gpus-per-node=1)
  if [[ "$partition" == DSPRHBM ]]; then resources=(--qos=rush-cpu --cpus-per-task=2); fi
  job=$(sbatch --parsable --partition="$partition" "${resources[@]}" \
    --job-name="elpa-simple-$partition" --output="$output/slurm-%j.log" \
    "$root/control/check-module.sbatch" "$root/control" "$output" "$mode")
  printf '%s\n' "$job" > "$output/job.id"
  printf '%s\t%s\t%s\n' "$partition" "$job" "$output" | tee -a "$root/jobs.tsv"
done
printf 'Native module results: %s\n' "$root"
