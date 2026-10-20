#!/usr/bin/env bash
set -euo pipefail
rank=${OMPI_COMM_WORLD_RANK:?}
trace=${SAI_NATIVE_TRACE:?}
binary="${SAI_LAMMPS_PREFIX:?}/bin/lmp"
[[ ! -d /.singularity.d ]]
[[ -n "${CUDA_VISIBLE_DEVICES:-}" && "$CUDA_VISIBLE_DEVICES" != -1 ]]
mkdir -p "${TMPDIR:?}"
printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$rank" "$(hostname -s)" "$binary" \
  "$CUDA_VISIBLE_DEVICES" "$(awk '/^Cpus_allowed_list:/ {print $2}' /proc/self/status)" \
  "$(sha256sum "$binary" | awk '{print $1}')" > "$trace/rank-$rank.tsv"
if [[ "${1:-}" == -h ]]; then
  ldd "$binary" > "$trace/ldd-$rank.txt"
  if grep -q 'not found' "$trace/ldd-$rank.txt"; then cat "$trace/ldd-$rank.txt" >&2; exit 1; fi
  nvidia-smi --query-gpu=name,driver_version --format=csv,noheader > "$trace/gpu-$rank.txt"
  awk '/^flags/ {print; exit}' /proc/cpuinfo > "$trace/cpu-$rank.txt"
fi
exec "$binary" "$@"
