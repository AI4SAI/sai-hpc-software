#!/bin/bash

_sai_fail() {
    echo "ERROR: $*" >&2
    exit 1
}

_sai_is_uint() {
    [[ "${1:-}" =~ ^[0-9]+$ ]]
}

_sai_cpu_count() {
    local spec item start end count=0
    spec="$(awk '/^Cpus_allowed_list:/ {print $2}' /proc/self/status 2>/dev/null)" || return 1
    [[ -n "$spec" ]] || return 1
    IFS=',' read -r -a _sai_cpu_items <<< "$spec"
    for item in "${_sai_cpu_items[@]}"; do
        if [[ "$item" =~ ^([0-9]+)$ ]]; then
            count=$((count + 1))
        elif [[ "$item" =~ ^([0-9]+)-([0-9]+)$ ]]; then
            start="${BASH_REMATCH[1]}"
            end="${BASH_REMATCH[2]}"
            (( start <= end )) || return 1
            count=$((count + end - start + 1))
        else
            return 1
        fi
    done
    printf '%s\n' "$count"
}

_sai_cleanup() {
    local status=$?
    trap - EXIT
    if [[ -n "${_SAI_MPS_STARTED:-}" ]]; then
        if (( SLURM_NNODES > 1 )); then
            pdsh -R ssh -w "$SLURM_NODELIST" "export CUDA_MPS_PIPE_DIRECTORY=${CUDA_MPS_PIPE_DIRECTORY}; export CUDA_MPS_LOG_DIRECTORY=${CUDA_MPS_LOG_DIRECTORY}; echo quit | nvidia-cuda-mps-control" >/dev/null 2>&1 || true
        else
            echo quit | nvidia-cuda-mps-control >/dev/null 2>&1 || true
        fi
    fi
    [[ -n "${_SAI_NCCL_TOPO_FILE:-}" ]] && rm -f "$_SAI_NCCL_TOPO_FILE"
    exit "$status"
}

trap _sai_cleanup EXIT

: "${GPUS_PER_SOCKET:?GPUS_PER_SOCKET is not set}"
: "${CORES_PER_SOCKET:?CORES_PER_SOCKET is not set}"
: "${SLURM_JOB_ID:?SLURM_JOB_ID is not set}"
: "${SLURM_NTASKS:?SLURM_NTASKS is not set}"
: "${SLURM_NNODES:?SLURM_NNODES is not set}"
: "${SLURM_GPUS_ON_NODE:?SLURM_GPUS_ON_NODE is not set}"

_sai_is_uint "$GPUS_PER_SOCKET" || _sai_fail "GPUS_PER_SOCKET=$GPUS_PER_SOCKET is not an integer."
_sai_is_uint "$CORES_PER_SOCKET" || _sai_fail "CORES_PER_SOCKET=$CORES_PER_SOCKET is not an integer."
_sai_is_uint "$SLURM_NTASKS" || _sai_fail "SLURM_NTASKS=$SLURM_NTASKS is not an integer."
_sai_is_uint "$SLURM_NNODES" || _sai_fail "SLURM_NNODES=$SLURM_NNODES is not an integer."
_sai_is_uint "$SLURM_GPUS_ON_NODE" || _sai_fail "SLURM_GPUS_ON_NODE=$SLURM_GPUS_ON_NODE is not an integer."

export THREADS_PER_CORE="${THREADS_PER_CORE:-2}"
_sai_is_uint "$THREADS_PER_CORE" || _sai_fail "THREADS_PER_CORE=$THREADS_PER_CORE is not an integer."

(( GPUS_PER_SOCKET > 0 )) || _sai_fail "GPUS_PER_SOCKET must be positive."
(( CORES_PER_SOCKET > 0 )) || _sai_fail "CORES_PER_SOCKET must be positive."
(( THREADS_PER_CORE > 0 )) || _sai_fail "THREADS_PER_CORE must be positive."
(( SLURM_NTASKS > 0 )) || _sai_fail "SLURM_NTASKS must be positive."
(( SLURM_NNODES > 0 )) || _sai_fail "SLURM_NNODES must be positive."
(( SLURM_GPUS_ON_NODE > 0 )) || _sai_fail "SLURM_GPUS_ON_NODE must be positive."
(( CORES_PER_SOCKET % GPUS_PER_SOCKET == 0 )) || _sai_fail "CORES_PER_SOCKET must be divisible by GPUS_PER_SOCKET."

export CORES_PER_GPU=$((CORES_PER_SOCKET / GPUS_PER_SOCKET))
_SAI_TOTAL_GPUS=$((SLURM_NNODES * SLURM_GPUS_ON_NODE))
(( SLURM_NTASKS % _SAI_TOTAL_GPUS == 0 )) || _sai_fail "SLURM_NTASKS=$SLURM_NTASKS is not divisible by total GPUs=$_SAI_TOTAL_GPUS."

export RANKS_PER_GPU=$((SLURM_NTASKS / _SAI_TOTAL_GPUS))
export RANKS_PER_NODE=$((SLURM_GPUS_ON_NODE * RANKS_PER_GPU))
(( RANKS_PER_GPU >= 1 )) || _sai_fail "RANKS_PER_GPU must be positive."
(( RANKS_PER_GPU <= CORES_PER_GPU )) || _sai_fail "RANKS_PER_GPU=$RANKS_PER_GPU exceeds CORES_PER_GPU=$CORES_PER_GPU."

_SAI_THREADS_PER_GPU=$((THREADS_PER_CORE * CORES_PER_GPU))
if (( CORES_PER_GPU % RANKS_PER_GPU == 0 )); then
    _SAI_PE=$((CORES_PER_GPU / RANKS_PER_GPU))
    export OMP_NUM_THREADS=$((THREADS_PER_CORE * _SAI_PE))
    export MAP_OPT="ppr:${RANKS_PER_NODE}:node:pe=${_SAI_PE}"
elif (( _SAI_THREADS_PER_GPU % RANKS_PER_GPU == 0 )); then
    _SAI_PE=$((_SAI_THREADS_PER_GPU / RANKS_PER_GPU))
    export OMP_NUM_THREADS=$_SAI_PE
    export MAP_OPT="ppr:${RANKS_PER_NODE}:node:hwtcpus:pe=${_SAI_PE}"
else
    _sai_fail "Neither cores nor hardware threads per GPU are divisible by RANKS_PER_GPU=$RANKS_PER_GPU."
fi

_SAI_EXPECTED_CPUS=$((SLURM_GPUS_ON_NODE * THREADS_PER_CORE * CORES_PER_GPU))
if _SAI_VISIBLE_CPUS="$(_sai_cpu_count)"; then
    (( _SAI_VISIBLE_CPUS == _SAI_EXPECTED_CPUS )) || _sai_fail "Cpus_allowed_list has $_SAI_VISIBLE_CPUS CPUs, expected $_SAI_EXPECTED_CPUS for $SLURM_GPUS_ON_NODE GPU(s)."
fi

if (( RANKS_PER_GPU > 1 )); then
    echo "INFO: Will utilize CUDA MPS and run $RANKS_PER_GPU Ranks-per-GPU."
    export CUDA_MPS_PIPE_DIRECTORY=/tmp/CUDA_MPS-Slurm_Job_${SLURM_JOB_ID}
    export CUDA_MPS_LOG_DIRECTORY=/tmp/CUDA_MPS-Slurm_Job_${SLURM_JOB_ID}
    echo "INFO: SLURM_NODELIST is $SLURM_NODELIST"
    echo "INFO: Setting CUDA_MPS_PIPE_DIRECTORY and CUDA_MPS_LOG_DIRECTORY to $CUDA_MPS_LOG_DIRECTORY"
    if (( SLURM_NNODES > 1 )); then
        pdsh -R ssh -w "$SLURM_NODELIST" "export CUDA_MPS_PIPE_DIRECTORY=${CUDA_MPS_PIPE_DIRECTORY}; export CUDA_MPS_LOG_DIRECTORY=${CUDA_MPS_LOG_DIRECTORY}; nvidia-cuda-mps-control -d" || _sai_fail "Failed to start CUDA MPS on all nodes."
    else
        nvidia-cuda-mps-control -d || _sai_fail "Failed to start CUDA MPS."
    fi
    _SAI_MPS_STARTED=1
fi

if (( SLURM_NNODES > 1 )); then
    _SAI_NCCL_TOPO_FILE="$(pwd)/.nccl_topo_${SLURM_JOB_ID}.xml"
    _SAI_NCCL_DUMMY_BIN=/opt/sai/runtime/ib_map.d/nccl_gdr_patch
    export NCCL_TOPO_DUMP_FILE="$_SAI_NCCL_TOPO_FILE"
    if "$_SAI_NCCL_DUMMY_BIN" >/dev/null 2>&1 && [[ -f "$NCCL_TOPO_DUMP_FILE" ]]; then
        sed -i 's/gdr="0"/gdr="1"/g' "$NCCL_TOPO_DUMP_FILE"
        export NCCL_TOPO_FILE="$NCCL_TOPO_DUMP_FILE"
        unset NCCL_TOPO_DUMP_FILE
        echo "[INFO] NCCL Topology dumped via C-API and successfully patched to force GDR=1."
    else
        unset NCCL_TOPO_DUMP_FILE
        echo "[WARN] Failed to dump NCCL Topology. GDR might still be disabled."
    fi
fi
