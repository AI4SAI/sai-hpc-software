#!/usr/bin/env bash
# Internal acceptance check, never an installed user template.
#SBATCH --time=00:15:00
set -eo pipefail
role=${1:?survivor or peer}
bundle=${2:?candidate bundle}
output=${3:?project check directory}
package=${4:?numerical check package}
driver=${5:?concurrent check Python script}
[[ $SLURM_JOB_ACCOUNT == proj-sai-hpc-software && $output == /org/org-open-sai/proj-sai-hpc-software/* ]]
source /etc/profile.d/lmod.sh
module purge
unset TMPDIR TMP TEMP APPTAINER_TMPDIR APPTAINER_CACHEDIR APPTAINER_CONFIGDIR
module use "$bundle/modulefiles"
module load cp2k-sif/master-20261024-gcfe5e5d87aaf
if [[ $SLURM_JOB_PARTITION == DSPRHBM ]]; then
  MAP_OPT=slot:pe=$SLURM_CPUS_PER_TASK
  threads=$SLURM_CPUS_PER_TASK
else
  source /opt/sai/runtime/mps_mapping.d/${SLURM_JOB_PARTITION}.bash
  threads=$CORES_PER_GPU
fi
export OMP_NUM_THREADS=$threads OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export SAI_SIF_TRACE_DIR="$output/trace"
if [[ $role == survivor ]]; then
  mpirun -np 1 --map-by "$MAP_OPT" /usr/bin/python3 -I -B "$driver" "$output" "$bundle" "$package"
else
  [[ $role == peer ]]
  for ((attempt=0; attempt<180; attempt++)); do
    [[ ! -f $output/ready.json ]] || break
    sleep 1
  done
  [[ -f $output/ready.json ]]
  mpirun -np 1 --map-by "$MAP_OPT" cp2k.psmp --version
fi
