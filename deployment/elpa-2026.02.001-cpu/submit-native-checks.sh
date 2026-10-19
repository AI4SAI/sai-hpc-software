#!/usr/bin/env bash
# Verify the administrator-installed CPU/GPU libraries and public auto module.
set -euo pipefail
package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
original=$package/../elpa-2026.02.001
work_root=${SAI_ELPA_NATIVE_WORK_ROOT:-"$HOME/sai-hpc-software"}
selection=${1:-all}
case "$selection" in
  all) partitions=(16V100 4V100 8V100V0 DSPRHBM) ;;
  16V100|4V100|8V100V0|DSPRHBM) partitions=("$selection") ;;
  *) echo "Usage: bash $0 [all|16V100|4V100|8V100V0|DSPRHBM]" >&2; exit 2 ;;
esac
python3 "$package/install.py" --verify
export ELPA_NATIVE_C_API=explicit
root="$work_root/runtime-tests/elpa-auto-physical-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$root/control"
cp "$original"/{verify-native.sbatch,smoke.c,verify-native-results.py} "$root/control/"
cp "$package"/{install.py,release.json,cpu-manifest.json,avx2-manifest.json,avx512-manifest.json,2026.02.001-2603-gnu-auto} "$root/control/"
(cd "$root/control" && sha256sum *) > "$root/controls.sha256"
for partition in "${partitions[@]}"; do
  output="$root/$partition"
  mkdir "$output"
  case "$partition" in
    16V100|8V100V0) isa=avx2 ;;
    4V100|DSPRHBM) isa=avx512 ;;
  esac
  backend=nvidia
  gpu_max=1
  resources=(--qos=flood-1o2gpu --gpus-per-node=1)
  if [[ "$partition" == DSPRHBM ]]; then
    backend=cpu
    gpu_max=0
    resources=(--qos=rush-cpu --cpus-per-task=2)
  fi
  job=$(sbatch --parsable --partition="$partition" "${resources[@]}" \
    --job-name="elpa-auto-native-$partition" --output="$output/slurm-%j.log" \
    "$root/control/verify-native.sbatch" "$root/control" "$output" "$work_root" "$isa" "$gpu_max" "$backend")
  printf '%s\n' "$job" > "$output/job.id"
  printf '%s\t%s\t%s\n' "$partition" "$job" "$output" | tee -a "$root/jobs.tsv"
done
printf 'Native auto module results: %s\n' "$root"
