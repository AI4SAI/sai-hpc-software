#!/usr/bin/env bash
set -euo pipefail
rank=${OMPI_COMM_WORLD_RANK:?}
trace=${SAI_NATIVE_TRACE:?}
binary="${SAI_CP2K_PREFIX:?}/bin/cp2k.psmp"
printf '%s\t%s\t%s\t%s\t%s\n' "$rank" "$(hostname -s)" "$binary" \
  "${CUDA_VISIBLE_DEVICES:-}" "$(awk '/^Cpus_allowed_list:/ {print $2}' /proc/self/status)" \
  > "$trace/rank-$rank.tsv"
if [[ "${1:-}" == --version ]]; then
  ldd "$binary" > "$trace/ldd-$rank.txt"
  if grep -q 'not found' "$trace/ldd-$rank.txt"; then cat "$trace/ldd-$rank.txt" >&2; exit 1; fi
  grep -F 'libhcoll.so.1 => /opt/devtools/nvidia/hpc_sdk/' "$trace/ldd-$rank.txt"
  export LD_DEBUG=libs LD_DEBUG_OUTPUT="$trace/loader-$rank"
fi
exec "$binary" "$@"
