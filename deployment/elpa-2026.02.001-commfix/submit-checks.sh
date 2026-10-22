#!/usr/bin/env bash
# Check each final candidate SIF on its actual deployment partitions.
set -euo pipefail
umask 022
package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
avx2=${1:?AVX2 run}; avx512=${2:?AVX512 run}; cpu=${3:?CPU run}
root=/home/stardust/sai-hpc-software
results=${4:-$root/runtime-tests/elpa-commfix-$(date +%Y%m%d-%H%M%S)}
[[ "$results" == "$root/runtime-tests/elpa-commfix-"* && "$results" != *..* ]]
stamp=${results##*/elpa-commfix-}
mkdir -p "$results"
for partition in 16V100 4V100 8V100V0 DSPRHBM; do
  # A failed later submission may be resumed without submitting earlier jobs again.
  if [[ -f "$results/$partition/job.id" ]]; then continue; fi
  case "$partition" in
    16V100|8V100V0) build=$avx2; target=avx2 ;;
    4V100) build=$avx512; target=avx512 ;;
    DSPRHBM) build=$cpu; target=dsprhbm ;;
  esac
  [[ "$build" == "$root/runs/elpa-"* && -f "$build/job.id" ]]
  run=$root/runs/elpa-commfix-check-$stamp-$partition
  mkdir -p "$run"/{control,work,runtime,cache}
  cp "$build/control/"{container.sh,environment.sh} "$run/control/"
  cp "$package/../elpa-2026.02.001/"{verify.sh,smoke.c,comm-trace.c,verify-lifecycle.py} "$run/control/"
  cp "$package/check.sbatch" "$run/control/"
  cp "$package/2026.02.001-2603-gnu-auto" "$run/control/"
  cp "$package/check-partition.sh" "$run/control/"
  cp "$package/../elpa-2026.02.001-cpu/2026.02.001-2603-gnu-auto" "$run/control/previous-auto"
  cp "$package/../elpa-2026.02.001/2026.02.001-2603-gnu-auto" "$run/control/legacy-auto"
  if [[ ! -L "$run/elpa-$target.sif" ]]; then ln -s "$build/elpa-$target.sif" "$run/elpa-$target.sif"; fi
  [[ $(readlink "$run/elpa-$target.sif") == "$build/elpa-$target.sif" ]]
  if [[ ! -L "$results/$partition" ]]; then ln -s "$run" "$results/$partition"; fi
  [[ $(readlink "$results/$partition") == "$run" ]]
  (cd "$run/control" && sha256sum *) > "$run/controls.sha256"
  resources=(--qos=flood-1o2gpu --gpus-per-node=1)
  if [[ "$partition" == DSPRHBM ]]; then resources=(--qos=rush-cpu --cpus-per-task=4); fi
  build_job=$(cat "$build/job.id")
  state=$(sacct -X -n -P -j "$build_job" --format=State,ExitCode)
  dependencies=()
  case "$state" in
    'COMPLETED|0:0') [[ -f "$build/accepted.txt" ]] ;;
    RUNNING\|*|PENDING\|*|COMPLETING\|*) dependencies=(--dependency="afterok:$build_job") ;;
    *) printf 'Build %s is not successful or active: %s\n' "$build_job" "$state" >&2; exit 1 ;;
  esac
  # Completed jobs can disappear from slurmctld before sacct expires them;
  # attaching afterok to that purged ID would reject an otherwise valid check.
  job=$(sbatch --parsable --partition="$partition" "${resources[@]}" "${dependencies[@]}" \
    --job-name="elpa-fix-check-$partition" \
    --output="$run/slurm-%j.log" "$run/control/check.sbatch" "$run" "$target" "$partition")
  printf '%s\n' "$job" > "$run/job.id"
  printf '%s\t%s\t%s\n' "$partition" "$job" "$run" | tee -a "$results/jobs.tsv"
done
printf 'ELPA repair checks: %s\n' "$results"
