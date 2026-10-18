#!/usr/bin/env bash
# Run after administrator installation, using the invoking account's home.
set -euo pipefail
package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
work_root=${SAI_ELPA_NATIVE_WORK_ROOT:-"$HOME/sai-hpc-software"}
selection=${1:-all}
case "$selection" in
  all) partitions=(16V100 4V100 8V100V0 DSPRHBM) ;;
  16V100|4V100|8V100V0|DSPRHBM) partitions=("$selection") ;;
  *) echo "Usage: bash $0 [all|16V100|4V100|8V100V0|DSPRHBM]" >&2; exit 2 ;;
esac
python3 "$package/install.py" --verify
root="$work_root/runtime-tests/elpa-physical-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$root/control"
cp "$package"/{verify-native.sbatch,smoke.c,install.py,release.json,avx2-manifest.json,avx512-manifest.json,2026.02.001-2603-gnu-auto} "$root/control/"
(cd "$root/control" && sha256sum *) > "$root/controls.sha256"
for partition in "${partitions[@]}"; do
  output="$root/$partition"
  mkdir "$output"
  case "$partition" in
    16V100|8V100V0) isa=avx2 ;;
    4V100|DSPRHBM) isa=avx512 ;;
  esac
  gpu_max=1
  resources=(--qos=flood-1o2gpu --gpus-per-node=1)
  if [[ "$partition" == DSPRHBM ]]; then
    gpu_max=0
    resources=(--qos=rush-cpu --cpus-per-task=2)
  fi
  job=$(sbatch --parsable --partition="$partition" "${resources[@]}" \
    --job-name="elpa-native-$partition" --output="$output/slurm-%j.log" \
    "$root/control/verify-native.sbatch" "$root/control" "$output" "$work_root" "$isa" "$gpu_max")
  printf '%s\n' "$job" > "$output/job.id"
  printf '%s\t%s\t%s\n' "$partition" "$job" "$output" | tee -a "$root/jobs.tsv"
done
printf 'Native check results: %s\n' "$root"
