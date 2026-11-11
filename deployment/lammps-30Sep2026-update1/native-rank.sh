#!/bin/bash
set -euo pipefail
binary=${SAI_LAMMPS_PREFIX:?}/bin/lmp
rank=${OMPI_COMM_WORLD_RANK:?}
cpu=$(awk '/^Cpus_allowed_list:/ {print $2}' /proc/self/status)
[[ -n "${CUDA_VISIBLE_DEVICES:-}" && "$CUDA_VISIBLE_DEVICES" != -1 && -n "$cpu" ]]
printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$rank" "$(hostname -s)" "$binary" \
    "$CUDA_VISIBLE_DEVICES" "$cpu" "$(sha256sum "$binary" | cut -d ' ' -f 1)" > "ranks/rank-$rank.tsv"
if [[ ${1:-} == -h ]]; then
    ldd "$binary" > "ranks/ldd-$rank.txt"
    ! grep -q 'not found' "ranks/ldd-$rank.txt"
fi
exec "$binary" "$@"
