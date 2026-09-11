#!/usr/bin/env bash
# Host-side MPI ranks enter the read-only SIF through this trusted launcher.
set -euo pipefail

: "${SAI_SOFTWARE_ROOT:?load the generated ABACUS module first}"
: "${SAI_ABACUS_VERSION:?load the generated ABACUS module first}"
: "${SLURM_JOB_PARTITION:?ABACUS auto selection requires a Slurm allocation}"

case "$SLURM_JOB_PARTITION" in
  DSPRHBM) target=dsprhbm; gpu=false ;;
  4V100) target=4v100-avx512; gpu=true ;;
  16V100) target=16v100-avx2; gpu=true ;;
  8V100V0) target=8v100v0-avx512; gpu=true ;;
  8A100M40) target=a100; gpu=true ;;
  *) echo "unsupported ABACUS partition: $SLURM_JOB_PARTITION" >&2; exit 2 ;;
esac

catalog="$SAI_SOFTWARE_ROOT/containers/software/abacus/$SAI_ABACUS_VERSION/$target"
if [[ -n "${SAI_ABACUS_IMAGE:-}" ]]; then
  image=$(realpath -e -- "$SAI_ABACUS_IMAGE")
  [[ "$image" == "$catalog"/*.sif && -f "$image" && ! -L "$SAI_ABACUS_IMAGE" ]] || {
    echo "pinned ABACUS image is outside the selected catalog" >&2
    exit 2
  }
else
  image="$catalog/current.sif"
fi
prefix="/opt/software/abacus/$SAI_ABACUS_VERSION/$target"
[[ -r "$image" && ! -L "$catalog" ]] || {
  echo "no verified ABACUS image for $SAI_ABACUS_VERSION on $target" >&2
  exit 2
}

workdir=$(pwd -P)
case "$workdir" in *:*|*,*|*$'\n'*) echo "working directory cannot contain ':', ',' or newline" >&2; exit 2;; esac
host_tmp=$(realpath -m -- "${TMPDIR:?set a host MPI TMPDIR below SAI_SOFTWARE_ROOT/runtime}")
runtime_test_root=$(realpath -m -- "$SAI_SOFTWARE_ROOT/runtime-tests")
runtime_job_root=$(realpath -m -- "$SAI_SOFTWARE_ROOT/runtime/jobs")
case "$host_tmp" in
  "$runtime_test_root"/*|"$runtime_job_root"/*) ;;
  *) echo "host MPI TMPDIR must stay below the SAI runtime roots" >&2; exit 2 ;;
esac
case "$host_tmp" in *:*|*,*|*$'\n'*) echo "host MPI TMPDIR contains unsafe bind characters" >&2; exit 2;; esac
[[ -d "$host_tmp" && ! -L "$host_tmp" ]] || {
  echo "host MPI TMPDIR must be an existing regular directory" >&2
  exit 2
}
job=${SLURM_JOB_ID:-manual}
node=${SLURM_NODEID:-0}
rank=${OMPI_COMM_WORLD_RANK:-${PMIX_RANK:-0}}
[[ "$job" =~ ^[A-Za-z0-9_.-]+$ && "$node" =~ ^[0-9]+$ && "$rank" =~ ^[0-9]+$ ]] || {
  echo "unsafe runtime identity" >&2
  exit 2
}
job_runtime="$SAI_SOFTWARE_ROOT/runtime/jobs/$job"
rank_runtime="$job_runtime/$node-$rank"
mkdir -p "$rank_runtime"
export APPTAINER_TMPDIR="$rank_runtime" APPTAINER_CACHEDIR="$rank_runtime/cache"
mkdir -p "$APPTAINER_CACHEDIR"
cleanup() {
  for _ in 1 2 3 4 5; do
    if rm -rf -- "$rank_runtime" 2>/dev/null; then
      rmdir -- "$job_runtime" 2>/dev/null || true
      return 0
    fi
    sleep 1
  done
  return 0
}
trap cleanup EXIT

if [[ -n "${SAI_ABACUS_TRACE_DIR:-}" ]]; then
  trace=$(realpath -m -- "$SAI_ABACUS_TRACE_DIR")
  allowed=$(realpath -m -- "$SAI_SOFTWARE_ROOT/runtime-tests")
  [[ "$trace" == "$allowed"/* && -d "$trace" && ! -L "$trace" ]] || {
    echo "trace directory must be an existing runtime-test directory" >&2
    exit 2
  }
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$(hostname)" "$rank" "$target" \
    "$(readlink -f -- "$image")" "${CUDA_VISIBLE_DEVICES:-}" "${OPAL_PREFIX:-}" \
    > "$trace/rank-$rank.tsv"
fi

args=(apptainer exec --cleanenv --no-home
      --no-mount bind-paths,home,cwd,tmp,hostfs --pwd /work)
if [[ "$gpu" == true ]]; then args+=(--nv); fi
for path in /usr /lib /lib64 /opt/devtools; do args+=(--bind "$path:$path:ro"); done
args+=(--bind "$workdir:/work:rw" --bind "$rank_runtime:/runtime:rw"
      --bind "$host_tmp:$host_tmp:rw" --env "TMPDIR=$host_tmp"
      --env "OMP_NUM_THREADS=${OMP_NUM_THREADS:-1}")

if [[ -n "${NCCL_TOPO_FILE:-}" ]]; then
  [[ -f "$NCCL_TOPO_FILE" && ! -L "$NCCL_TOPO_FILE" ]] || {
    echo "NCCL_TOPO_FILE is not a regular file" >&2
    exit 2
  }
  args+=(--bind "$NCCL_TOPO_FILE:/runtime/nccl-topology.xml:ro"
        --env NCCL_TOPO_FILE=/runtime/nccl-topology.xml)
fi

# Open MPI / PRRTE / PMIx, CUDA and communication settings are established by
# the host launcher. Pass only those families through cleanenv.
while IFS= read -r name; do
  case "$name" in
    SLURM_*|OMPI_*|OPAL_*|PMIX_*|PMI_*|PRTE_*|UCX_*|NCCL_*|CUSOLVERMP_*|CUDA_*|NVIDIA_VISIBLE_DEVICES|FI_*|OMP_*)
      [[ "$name" != NCCL_TOPO_FILE ]] || continue
      [[ "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || exit 2
      args+=(--env "$name=${!name}")
      ;;
  esac
done < <(compgen -e)

set +e
"${args[@]}" "$image" /usr/bin/bash --noprofile --norc -c \
  'apptainer_driver_path=${LD_LIBRARY_PATH:-}; source "$1"; shift; export LD_LIBRARY_PATH="$apptainer_driver_path${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"; exec "$@"' bash \
  "$prefix/share/sai/runtime-env.sh" "$prefix/bin/abacus" "$@"
status=$?
set -e
exit "$status"
