#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=00:15:00
#SBATCH --export=NIL
set -euo pipefail
run=${1:?}; target=${2:?}; partition=${3:?}
export PATH=/usr/bin:/bin LD_LIBRARY_PATH= LD_PRELOAD=
export TMPDIR=$run/runtime APPTAINER_TMPDIR=$run/runtime APPTAINER_CACHEDIR=$run/cache
export BUILD_JOBS=$(nproc)
[[ "$SLURM_JOB_PARTITION" == "$partition" ]]
mapping=/opt/sai_config/mps_mapping.d/$partition.bash
if [[ -r "$mapping" ]]; then set +u; source "$mapping"; set -u; fi
hostname > "$run/hostname.txt"
scontrol show job -o "$SLURM_JOB_ID" > "$run/allocation.txt"
sha256sum "$run/elpa-$target.sif" > "$run/artifact.sha256"
bash "$run/control/container.sh" verify "$run" "$target" bash /control/verify.sh "$target"
bash "$run/control/container.sh" verify "$run" "$target" \
  bash /control/check-partition.sh /control /workspace/module "$partition"
printf 'ELPA_REPAIR_PARTITION_PASS %s\n' "$partition" > "$run/accepted.txt"
