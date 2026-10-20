#!/usr/bin/env bash
# Resources and output path are supplied by submit.sh.
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --qos=flood-1o2gpu
#SBATCH --time=04:00:00
#SBATCH --export=NIL
set -euo pipefail
run=${1:?}; isa=${2:?}
umask 022
export PATH=/usr/bin:/bin LD_LIBRARY_PATH= LD_PRELOAD=
export TMPDIR=$run/runtime APPTAINER_TMPDIR=$run/runtime APPTAINER_CACHEDIR=$run/cache
export BUILD_JOBS=$(nproc)
# Site GPU allocation mapping, when present.
mapping=/opt/sai_config/mps_mapping.d/${SLURM_JOB_PARTITION}.bash
if [[ -r "$mapping" ]]; then set +u; source "$mapping"; set -u; fi
printf 'ELPA_JOB %s %s %s %s\n' "$SLURM_JOB_ID" "$isa" "$(hostname)" "$(date -Is)"
printf 'ELPA_CPU_ALLOCATION jobs=%s cpus_on_node=%s\n' "$BUILD_JOBS" "${SLURM_CPUS_ON_NODE:-unknown}"
taskset -pc $$
bash "$run/control/container.sh" build "$run" "$isa" bash /control/build.sh "$isa"
apptainer=/opt/devtools/apptainer/1.4.4/bin/apptainer
"$apptainer" sif new "$run/elpa-$isa.sif"
"$apptainer" sif add --datatype 4 --partfs 1 --parttype 2 --partarch 2 --groupid 1 "$run/elpa-$isa.sif" "$run/work/elpa.squashfs"
bash "$run/control/container.sh" verify "$run" "$isa" bash /control/verify.sh "$isa"
sha256sum "$run/elpa-$isa.sif" > "$run/artifact.sha256"
chmod 0444 "$run/elpa-$isa.sif"
printf 'ELPA_BUILD_AND_CHECKS_PASSED %s %s\n' "$isa" "$(date -Is)" | tee "$run/accepted.txt"
