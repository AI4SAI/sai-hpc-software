#!/usr/bin/env bash
set -euo pipefail
rank=${OMPI_COMM_WORLD_RANK:?}
local_rank=${OMPI_COMM_WORLD_LOCAL_RANK:?}
[[ "$rank" =~ ^[0-9]+$ && "$local_rank" =~ ^[0-9]+$ ]] || exit 2
host=$(hostname -s)
affinity=$(awk '/^Cpus_allowed_list:/ { print $2 }' /proc/self/status)
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$host" "$rank" "$local_rank" \
  "${OMP_NUM_THREADS:?}" "$affinity" "${CUDA_VISIBLE_DEVICES:-}" "${SLURM_JOB_ID:?}" \
  > "$SAI_BENCH_RESOURCES/rank-$rank.tsv"
topology="$SAI_BENCH_RESOURCES/topology-$rank.tsv"
: > "$topology"
IFS=',' read -r -a spans <<< "$affinity"
for span in "${spans[@]}"; do
  first=${span%-*}; last=${span#*-}
  for ((cpu=first; cpu<=last; cpu++)); do
    base="/sys/devices/system/cpu/cpu$cpu/topology"
    read -r socket < "$base/physical_package_id"
    read -r core < "$base/core_id"
    read -r siblings < "$base/thread_siblings_list"
    printf '%s\t%s\t%s\t%s\n' "$cpu" "$socket" "$core" "$siblings" >> "$topology"
  done
done
exec "$SAI_BENCH_BINARY" "$@"
