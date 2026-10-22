#!/usr/bin/env bash
# Both routes inherit the same host MPI rank, allocation and thread settings.
set -euo pipefail
mode=${1:?native or sif}; operation=${2:?cp2k or probe}; shift 2
[[ ${OMPI_COMM_WORLD_RANK:?} == 0 && ${OMPI_COMM_WORLD_SIZE:?} == 1 ]]
printf '%s\t%s\t%s\t%s\t%s\n' "$(id -u)" "$(hostname -s)" \
    "$(awk '/^Cpus_allowed_list:/ {print $2}' /proc/self/status)" \
    "${CUDA_VISIBLE_DEVICES:-}" "$OMP_NUM_THREADS" > rank.tsv
prefix=${SAI_CP2K_PREFIX:?}
if [[ $operation == probe ]]; then
    command=(/usr/bin/python3 probe.py "$prefix")
else
    [[ $operation == cp2k ]]
    command=("$prefix/bin/cp2k.psmp" "$@")
fi
if [[ $mode == native ]]; then exec "${command[@]}"; fi
if [[ $mode == checked ]]; then
    [[ $operation == cp2k && ${1:-} == --version ]]
    exec bash "${SAI_CHECKED_LAUNCHER:?}" --version
fi
[[ $mode == sif ]]
workdir=$(pwd -P)
args=(apptainer exec --cleanenv --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd "$workdir")
[[ $SLURM_JOB_PARTITION == DSPRHBM ]] || args+=(--nv)
for path in /usr /lib /lib64 /opt/devtools; do args+=(--bind "$path:$path:ro"); done
args+=(--bind "$workdir:$workdir:rw" --bind "$TMPDIR:$TMPDIR:rw")
if [[ -n ${NCCL_TOPO_FILE:-} && $NCCL_TOPO_FILE != "$TMPDIR/"* ]]; then
    args+=(--bind "$NCCL_TOPO_FILE:$NCCL_TOPO_FILE:ro")
fi
args+=(--env "LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/.singularity.d/libs"
       --env "CP2K_DATA_DIR=$CP2K_DATA_DIR" --env "TMPDIR=$TMPDIR"
       --env "XDG_CACHE_HOME=$XDG_CACHE_HOME" --env "MPI_HOME=$MPI_HOME"
       --env "OPENBLAS_NUM_THREADS=$OPENBLAS_NUM_THREADS" --env "MKL_NUM_THREADS=$MKL_NUM_THREADS")
while IFS= read -r name; do
    case "$name" in
        SLURM_*|OMPI_*|OPAL_*|PMIX_*|PMI_*|PRTE_*|UCX_*|NCCL_*|CUDA_*|NVIDIA_VISIBLE_DEVICES|FI_*|OMP_*)
            args+=(--env "$name=${!name}") ;;
    esac
done < <(compgen -e)
exec "${args[@]}" "${SAI_OVERHEAD_IMAGE:?}" "${command[@]}"
