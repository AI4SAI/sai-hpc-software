#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --export=ALL
set -eo pipefail
run=${1:?run}
export PATH=/usr/bin:/bin LD_LIBRARY_PATH= LD_PRELOAD= PYTHONDONTWRITEBYTECODE=1
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/devtools
module load apptainer/1.4.4
set -u
case "$SLURM_JOB_PARTITION" in
  4V100) target=4v100-avx512 ;;
  16V100) target=16v100-avx2 ;;
  *) exit 2 ;;
esac
project=/home/stardust/sai-hpc-software
original=/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/$SLURM_JOB_PARTITION
dependency_run=$project/runs/39365139034-1-release-$target-2026-10-20-67b5da876dd6-dependencies
export TMPDIR=$run/runtime APPTAINER_TMPDIR=$run/runtime APPTAINER_CACHEDIR=$run/cache
mkdir -p "$run/runtime" "$run/cache"
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
scontrol show job -o "$SLURM_JOB_ID" > "$run/results/allocation.txt"
lscpu > "$run/results/lscpu.txt"
build_jobs=$(python3 - <<'PY'
import os
from pathlib import Path
cores = set()
for cpu in os.sched_getaffinity(0):
    t = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
    cores.add(((t/'physical_package_id').read_text(), (t/'core_id').read_text()))
print(len(cores))
PY
)
args=(exec --userns --cleanenv --containall --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd "$run/work"
  --overlay "$dependency_run/work.ext3:ro")
for path in /usr /lib /lib64 /etc/alternatives /opt/devtools /opt/modules /etc/profile.d/lmod.sh /etc/lmod; do
  [[ ! -e "$path" ]] || args+=(--bind "$path:$path:ro")
done
args+=(--bind "$run:$run:rw" --bind "$run/control:$run/control:ro" --bind "$run/input:$run/input:ro"
  --bind "$project/cache/repositories/cp2k:$project/cache/repositories/cp2k:ro"
  --bind "$project/cache/cp2k-probe:$project/cache/cp2k-probe:ro"
  --bind "$project/cache/cp2k-dependencies:$project/cache/cp2k-dependencies:ro"
  --bind "$run/runtime:/tmp:rw" --bind "$run/runtime:/var/tmp:rw"
  --env "TMPDIR=$run/runtime" --env "BUILD_JOBS=$build_jobs")
python3 "$run/control/controller/cp2k_image_driver.py" "$project" "$run" "${args[@]}" \
  "$project/containers/base/minimal-v1.sif" /bin/bash \
  "$run/control/deployment/cp2k-water-scaling/build-cpu.sh" "$run" "$SLURM_JOB_PARTITION" "$original"
