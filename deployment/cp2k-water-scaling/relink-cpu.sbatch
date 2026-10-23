#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --export=ALL
set -eo pipefail
run=${1:?run}; original=${2:?original CPU build}
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
dependency_run=$project/runs/39365139034-1-release-$target-2026-10-20-67b5da876dd6-dependencies
export TMPDIR=$run/runtime APPTAINER_TMPDIR=$run/runtime APPTAINER_CACHEDIR=$run/cache
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
scontrol show job -o "$SLURM_JOB_ID" > "$run/results/allocation.txt"
lscpu > "$run/results/lscpu.txt"
args=(exec --userns --cleanenv --containall --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd "$run/work"
  --overlay "$dependency_run/work.ext3:ro")
for path in /usr /lib /lib64 /etc/alternatives /opt/devtools /opt/modules /etc/profile.d/lmod.sh /etc/lmod; do
  [[ ! -e "$path" ]] || args+=(--bind "$path:$path:ro")
done
args+=(--bind "$run:$run:rw" --bind "$run/control:$run/control:ro" --bind "$original:$original:ro"
  --bind "$run/runtime:/tmp:rw" --bind "$run/runtime:/var/tmp:rw" --env "TMPDIR=$run/runtime")
python3 "$run/control/controller/cp2k_image_driver.py" "$project" "$run" "${args[@]}" \
  "$project/containers/base/minimal-v1.sif" /bin/bash \
  "$run/control/deployment/cp2k-water-scaling/relink-env.sh" "$run" "$original"
