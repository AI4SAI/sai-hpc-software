#!/usr/bin/env bash
# Submit after installation, as the normal cluster user.
set -euo pipefail
package_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
project_root=${SAI_SOFTWARE_ROOT:-/home/stardust/sai-hpc-software}
selection=${1:-all}
case "$selection" in
  all) partitions=(4V100 16V100 8V100V0 DSPRHBM) ;;
  4V100|16V100|8V100V0|DSPRHBM) partitions=("$selection") ;;
  *) echo "Usage: bash $0 [all|4V100|16V100|8V100V0|DSPRHBM]" >&2; exit 2 ;;
esac
# Verify all installed bytes/module before reserving compute resources.
python3 "$package_dir/install.py" --verify
check_root="$project_root/runtime-tests/cp2k-2026.2-physical-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$check_root"
for partition in "${partitions[@]}"; do
  output="$check_root/$partition"
  mkdir "$output"
  resources=(--qos=flood-1o2gpu --gpus-per-node=1)
  if [[ "$partition" == DSPRHBM ]]; then resources=(--qos=rush-cpu --cpus-per-task=2); fi
  job=$(sbatch --parsable --partition="$partition" "${resources[@]}" \
    --job-name="cp2k-native-$partition" --output="$output/slurm-%j.log" \
    "$package_dir/verify-native.sbatch" "$package_dir" "$project_root" "$output")
  printf '%s\n' "$job" > "$output/job.id"
  printf '%s\t%s\t%s\n' "$partition" "$job" "$output" | tee -a "$check_root/jobs.tsv"
done
printf 'Native check results: %s\n' "$check_root"
