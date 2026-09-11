#!/usr/bin/env bash
# Host orchestration only. All software execution enters the immutable SIF.
set -euo pipefail
run_id=$1
control=$(cd -- "$(dirname -- "$0")" && pwd -P)
root="$SAI_SOFTWARE_ROOT/experimental/deepmd-lammps"
task="$root/runtime-tests/$run_id"
[[ "$run_id" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ && -d "$task" ]]
export SAI_MD_TEST_CONTROL="$control" OMP_NUM_THREADS=1 SAI_MD_TRACE_RANKS=1
export TMPDIR="$task/runtime" APPTAINER_TMPDIR="$task/runtime" APPTAINER_CACHEDIR="$task/runtime/cache"
mkdir -p "$task/case" "$task/results" "$APPTAINER_CACHEDIR"
source /etc/profile.d/lmod.sh
module use /opt/modules/modulefiles/devtools
module load apptainer/1.4.4 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto
case "$SLURM_JOB_PARTITION" in
  4V100) target=4v100-avx512 ;;
  16V100) target=16v100-avx2 ;;
  8V100V0) target=8v100v0-avx512 ;;
  *) exit 2 ;;
esac
prefix="/opt/software/lammps/$SAI_MD_VERSION/$target"
# Copy only packaged scientific task inputs; never extract an install/source
# tree to host. This is the same user-input bind model as normal workloads.
apptainer exec --cleanenv --no-home --no-mount bind-paths,home,cwd,tmp,hostfs \
  --bind /usr:/usr:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro \
  --bind "$task/case:/work:rw" --pwd /work "$SAI_MD_IMAGE" \
  /usr/bin/cp -a "$prefix/share/sai/smoke/." /work/
resources=$(python3 -c 'import json,os; print(json.dumps({"nodes":int(os.environ["SLURM_JOB_NUM_NODES"]),"ranks":int(os.environ["SAI_MD_ACCEPTANCE_RANKS"]),"gpus_per_node":1,"omp_threads":1}))')
export OMPI_MCA_rmaps_base_oversubscribe=1
launcher=$(python3 -c 'import json,os,sys; print(json.dumps(["mpirun","-np",os.environ["SAI_MD_ACCEPTANCE_RANKS"],"--map-by","ppr:2:node","bash",sys.argv[1]]))' "$control/md_runtime.sh")
cd "$task/case"
for implementation in baseline candidate; do
  export SAI_MD_IMPLEMENTATION="$implementation"
  for backend in tf pt jax; do
    # The SIF has trusted read-only /control only for this verifier, not as an
    # installation/runtime dependency of normal lmp or dp commands.
    bash "$control/md_runtime.sh" python /control/md_science.py python-eval /work \
      --backend "$backend" --implementation "$implementation" --resources-json "$resources" \
      --records "$implementation-$backend-python.json" \
      > "$task/results/$implementation-$backend-python.log" 2>&1
    python3 "$control/md_science.py" lammps-run "$task/case" --backend "$backend" \
      --implementation "$implementation" --resources-json "$resources" \
      --executable lmp --launcher-json "$launcher" \
      --records "$task/results/$implementation-$backend-lammps.json" \
      --output-dir "$task/results/$implementation-$backend-trials" \
      > "$task/results/$implementation-$backend-lammps.log" 2>&1
    cp "$task/case/$implementation-$backend-python.json" "$task/results/"
  done
done
python3 "$control/md_science.py" verify "$task/results/"*-python.json "$task/results/"*-lammps.json \
  > "$task/results/scientific-summary.json"
echo MD_ALL_BACKENDS_SCIENCE_COMPLETED_NOT_PUBLISHED
