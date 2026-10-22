#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --partition=DSPRHBM
#SBATCH --qos=rush-cpu
#SBATCH --time=01:00:00
#SBATCH --export=NIL
set -euo pipefail
run=${1:?home run directory}
umask 022
export PATH=/usr/bin:/bin LD_LIBRARY_PATH= LD_PRELOAD=
export TMPDIR=$run/runtime APPTAINER_TMPDIR=$run/runtime APPTAINER_CACHEDIR=$run/cache
export BUILD_JOBS=$(nproc)
printf 'ELPA_CPU_JOB %s %s jobs=%s %s\n' "$SLURM_JOB_ID" "$(hostname)" "$BUILD_JOBS" "$(date -Is)"
taskset -pc $$
bash "$run/control/container.sh" build "$run" dsprhbm bash /control/build.sh dsprhbm
apptainer=/opt/devtools/apptainer/1.4.4/bin/apptainer
"$apptainer" sif new "$run/elpa-dsprhbm.sif"
"$apptainer" sif add --datatype 4 --partfs 1 --parttype 2 --partarch 2 --groupid 1 "$run/elpa-dsprhbm.sif" "$run/work/elpa.squashfs"
bash "$run/control/container.sh" verify "$run" dsprhbm bash /control/verify.sh dsprhbm
sha256sum "$run/elpa-dsprhbm.sif" > "$run/artifact.sha256"
chmod 0444 "$run/elpa-dsprhbm.sif"
printf 'ELPA_BUILD_AND_CHECKS_PASSED dsprhbm %s\n' "$(date -Is)" | tee "$run/accepted.txt"
