#!/usr/bin/env bash
# Host-MPI launcher for a verified CP2K SIF.
set -euo pipefail
: "${SAI_SOFTWARE_ROOT:?load the generated CP2K module first}"
: "${SAI_CP2K_VERSION:?load the generated CP2K module first}"
: "${SLURM_JOB_PARTITION:?CP2K auto selection requires a Slurm allocation}"

case "$SLURM_JOB_PARTITION" in
  DSPRHBM) target=dsprhbm; gpu=false ;;
  4V100) target=4v100-avx512; gpu=true ;;
  16V100) target=16v100-avx2; gpu=true ;;
  8A100M40) target=a100; gpu=true ;;
  *) echo "unsupported CP2K partition: $SLURM_JOB_PARTITION" >&2; exit 2 ;;
esac

catalog="$SAI_SOFTWARE_ROOT/containers/software/cp2k/$SAI_CP2K_VERSION/$target"
if [[ -n "${SAI_CP2K_IMAGE:-}" ]]; then
  image=$(realpath -e -- "$SAI_CP2K_IMAGE")
  [[ "$image" == "$catalog"/*.sif && -f "$image" && ! -L "$SAI_CP2K_IMAGE" ]] || exit 2
else
  image="$catalog/current.sif"
fi
prefix="/opt/software/cp2k/$SAI_CP2K_VERSION/$target"
[[ -r "$image" && ! -L "$catalog" ]] || { echo "no verified CP2K image" >&2; exit 2; }

workdir=$(pwd -P)
host_tmp=$(realpath -m -- "${TMPDIR:?set a host MPI TMPDIR below SAI_SOFTWARE_ROOT/runtime}")
runtime_root=$(realpath -m -- "$SAI_SOFTWARE_ROOT/runtime")
case "$host_tmp" in "$runtime_root"/*) ;; *) echo "unsafe MPI TMPDIR" >&2; exit 2 ;; esac
[[ -d "$host_tmp" && ! -L "$host_tmp" ]] || exit 2
job=${SLURM_JOB_ID:-manual}; node=${SLURM_NODEID:-0}; rank=${OMPI_COMM_WORLD_RANK:-${PMIX_RANK:-0}}
[[ "$job" =~ ^[A-Za-z0-9_.-]+$ && "$node" =~ ^[0-9]+$ && "$rank" =~ ^[0-9]+$ ]] || exit 2
job_runtime="$SAI_SOFTWARE_ROOT/runtime/jobs/$job"
rank_runtime="$job_runtime/$node-$rank"
mkdir -p "$rank_runtime/cache"
export APPTAINER_TMPDIR="$rank_runtime" APPTAINER_CACHEDIR="$rank_runtime/cache"
cleanup() { rm -rf -- "$rank_runtime" 2>/dev/null || true; rmdir -- "$job_runtime" 2>/dev/null || true; }
trap cleanup EXIT

args=(apptainer exec --cleanenv --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd /work)
[[ "$gpu" == true ]] && args+=(--nv)
for path in /usr /lib /lib64 /opt/devtools /opt/apps; do args+=(--bind "$path:$path:ro"); done
args+=(--bind "$workdir:/work:rw" --bind "$rank_runtime:/runtime:rw"
       --bind "$host_tmp:$host_tmp:rw" --env "TMPDIR=$host_tmp"
       --env "OMP_NUM_THREADS=${OMP_NUM_THREADS:-1}")
while IFS= read -r name; do
  case "$name" in
    SLURM_*|OMPI_*|OPAL_*|PMIX_*|PMI_*|PRTE_*|UCX_*|NCCL_*|CUDA_*|NVIDIA_VISIBLE_DEVICES|FI_*|OMP_*)
      [[ "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || exit 2
      args+=(--env "$name=${!name}") ;;
  esac
done < <(compgen -e)
"${args[@]}" "$image" /usr/bin/bash --noprofile --norc -c \
  'driver=${LD_LIBRARY_PATH:-}; source "$1"; export LD_LIBRARY_PATH="$driver${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"; exec "$2" "${@:3}"' \
  bash "$prefix/share/sai/runtime-env.sh" "$prefix/bin/cp2k.psmp" "$@"
