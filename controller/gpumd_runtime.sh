#!/usr/bin/env bash
# One copied host launcher for each installed executable. No host install tree.
set -euo pipefail
: "${SAI_SOFTWARE_ROOT:?load the GPUMD module first}"
: "${SAI_GPUMD_VERSION:?load the GPUMD module first}"
name=${SAI_GPUMD_EXECUTABLE:-$(basename -- "$0")}
case "$name" in gpumd|nep|gnep) ;; *) echo 'unknown GPUMD executable' >&2; exit 2;; esac
case "${SLURM_JOB_PARTITION:?GPUMD requires a Slurm GPU allocation}" in
  4V100) target=4v100-avx512;; 16V100) target=16v100-avx2;; 8V100V0) target=8v100v0-avx512;; *) exit 2;;
esac
[[ "$SAI_GPUMD_VERSION" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]
catalog="$SAI_SOFTWARE_ROOT/containers/software/gpumd/$SAI_GPUMD_VERSION/$target"
image=${SAI_GPUMD_IMAGE:-$catalog/current.sif}
resolved=$(realpath -e -- "$image")
[[ "$resolved" == "$catalog"/*.sif && -f "$resolved" ]] || exit 2
prefix="/opt/software/gpumd/$SAI_GPUMD_VERSION/$target"
work=$(pwd -P)
case "$work" in *:*|*,*|*$'\n'*) exit 2;; esac
runtime=$(realpath -e -- "${TMPDIR:?set TMPDIR below a SAI runtime root}")
case "$runtime" in "$SAI_SOFTWARE_ROOT/runtime-tests/"*|"$SAI_SOFTWARE_ROOT/runtime/jobs/"*) ;; *) exit 2;; esac
case "$runtime" in *:*|*,*|*$'\n'*) exit 2;; esac
export APPTAINER_TMPDIR="$runtime" APPTAINER_CACHEDIR="$runtime/cache"
mkdir -p "$APPTAINER_CACHEDIR"
args=(apptainer exec --nv --cleanenv --containall --no-home
      --no-mount bind-paths,home,cwd,tmp,hostfs --pwd /work)
for dependency in /usr /lib /lib64 /opt/devtools /opt/apps; do args+=(--bind "$dependency:$dependency:ro"); done
args+=(--bind "$work:/work:rw" --bind "$runtime:/runtime:rw" --env TMPDIR=/runtime
       --env "OMP_NUM_THREADS=${OMP_NUM_THREADS:-2}")
for key in CUDA_VISIBLE_DEVICES DP_INTRA_OP_PARALLELISM_THREADS DP_INTER_OP_PARALLELISM_THREADS; do
  if [[ -v "$key" ]]; then args+=(--env "$key=${!key}"); fi
done
exec "${args[@]}" "$resolved" /usr/bin/bash --noprofile --norc -c \
  'driver=${LD_LIBRARY_PATH:-}; source "$1"; shift; export LD_LIBRARY_PATH="$driver${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"; exec "$@"' \
  bash "$prefix/share/sai/runtime-env.sh" "$prefix/bin/$name" "$@"
