#!/usr/bin/env bash
# Host orchestration; baseline and candidate calculations run inside the pinned SIF.
set -euo pipefail
run_id=$1
science_run=$2
[[ "$run_id" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ && "$science_run" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]
control=$(cd -- "$(dirname -- "$0")" && pwd -P)
root="$SAI_SOFTWARE_ROOT/experimental/deepmd-lammps"
task="$root/runtime-tests/$run_id"
export SAI_MD_TEST_CONTROL="$control" OMP_NUM_THREADS=1
export TMPDIR="$task/runtime" APPTAINER_TMPDIR="$task/runtime" APPTAINER_CACHEDIR="$task/runtime/cache"
mkdir -p "$APPTAINER_CACHEDIR"
source /etc/profile.d/lmod.sh
module use /opt/modules/modulefiles/devtools
module load apptainer/1.4.4 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto
# These are scientific inputs retained by the successful source-verified smoke.
cp -a "$root/runtime-tests/$science_run/case/." "$task/case/"
nsys=$(realpath -e /opt/devtools/nvidia/cuda-12.9.1/nsight-systems-2025.1.3/bin/nsys)
[[ "$nsys" == /opt/devtools/* && -x "$nsys" ]]
printf '%s\n' "$nsys" > "$task/results/nsys.path"
resources='{"nodes":1,"ranks":1,"gpus_per_node":1,"threads_per_rank":1}'
for backend in tf pt jax; do
  python3 -u "$control/md_performance_run.py" "$task/case" --backend "$backend" \
    --resources-json "$resources" --launcher "$control/md_runtime.sh" \
    --output-dir "$task/results/$backend" --nsys "$nsys" \
    > "$task/results/$backend.log" 2>&1
done
echo MD_PERFORMANCE_RAW_EVIDENCE_COLLECTED
