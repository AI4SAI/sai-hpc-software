#!/usr/bin/env bash
set -euo pipefail
rank=${OMPI_COMM_WORLD_RANK:?}
trace=${SAI_NATIVE_TRACE:?}
binary="${SAI_ABACUS_PREFIX:?}/bin/abacus"
[[ ! -d /.singularity.d ]]
[[ "$SAI_ABACUS_NATIVE_PARTITION" == "$SLURM_JOB_PARTITION" ]]
if [[ "$SLURM_JOB_PARTITION" != DSPRHBM ]]; then
    [[ -n "${CUDA_VISIBLE_DEVICES:-}" && "$CUDA_VISIBLE_DEVICES" != -1 ]]
fi
mkdir -p "${TMPDIR:?}"
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$rank" "$(hostname -s)" "$binary" \
  "${CUDA_VISIBLE_DEVICES:-}" "$(awk '/^Cpus_allowed_list:/ {print $2}' /proc/self/status)" \
  "$(sha256sum "$binary" | awk '{print $1}')" "${OPAL_PREFIX:?}" "${OMP_NUM_THREADS:?}" > "$trace/rank-$rank.tsv"
ldd "$binary" > "$trace/ldd-$rank.txt"
if grep -q 'not found' "$trace/ldd-$rank.txt"; then cat "$trace/ldd-$rank.txt" >&2; exit 1; fi
exec "$binary" "$@"
