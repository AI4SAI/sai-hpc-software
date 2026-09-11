#!/usr/bin/env bash
# Trusted host driver: host MPI launches the candidate SIF on two GPU nodes.
set -euo pipefail
task=$1
image=$2
launcher=$3
prefix=$4
controller=$5
run_id=$6
export SAI_ABACUS_IMAGE="$image"
export NCCL_DEBUG=TRACE NCCL_DEBUG_SUBSYS=INIT,COLL
export CUSOLVERMP_LOG_LEVEL=5 CUSOLVERMP_LOG_MASK=31
# Leave logging on stdout/stderr so both ranks' actual library calls are kept
# in abacus.log. No per-rank /tmp files, source extraction, or host executable.
unset NCCL_DEBUG_FILE CUSOLVERMP_LOG_FILE
nvidia-smi -L
for feature in cusolvermp nccl; do
  case_dir="$task/cases/$feature"
  result_dir="$task/results/$feature"
  mkdir -p "$case_dir" "$result_dir/ranks"
  apptainer exec --cleanenv --no-home \
    --no-mount bind-paths,home,cwd,tmp,hostfs --pwd /work \
    --bind /usr:/usr:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro \
    --bind "$case_dir:/work:rw" "$image" /usr/bin/cp -a \
    "$prefix/share/sai/gpu-cases/$feature/." /work/
  cd "$case_dir"
  export SAI_ABACUS_TRACE_DIR="$result_dir/ranks"
  mpirun -np 2 --map-by "$MAP_OPT" --report-bindings "$launcher" \
    > "$result_dir/abacus.log" 2>&1
done
python3 "$controller" verify "$run_id"
echo MULTINODE_GPU_FEATURES_VERIFIED
