#!/usr/bin/env bash
#SBATCH --job-name=module-publication-probe
#SBATCH --partition=DSPRHBM
#SBATCH --qos=rush-cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --time=2
#SBATCH --export=NIL
# No executable/scientific SIF is built or run. Test real Lmod semantics only.
set -eo pipefail
export USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?}
export PATH=/usr/bin:/bin LD_LIBRARY_PATH= LD_PRELOAD=
source /etc/profile.d/lmod.sh
module purge
set -u
probe_root=/home/stardust/sai-hpc-software/experimental/module-publication-${SLURM_JOB_ID:?}
probe_control=${1:?pass the absolute controller snapshot directory}
[[ "$probe_control" == /home/stardust/sai-hpc-software/controller/* && "$probe_control" != *..* ]]
# Site Lmod intentionally unsets the last LD_LIBRARY_PATH entry on unload;
# its shell wrapper is not nounset-safe when a later module is loaded.
sai_module() {
  set +u
  module "$@"
  module_status=$?
  set -u
  return "$module_status"
}
module_name=$(/usr/bin/python3 "$probe_control/probe_module_publication.py" "$probe_root" initial)
build_id=${module_name##*/}
sai_module use "$probe_root/modulefiles/apps"
sai_module load "$module_name"
[[ "$SAI_CP2K_IMAGE" == "$probe_root/containers/software/cp2k/development/$build_id/DSPRHBM/cpu-first.sif" ]]
[[ "$(command -v cp2k)" == "$probe_root/controller/cpu-first/cp2k" ]]
sai_module -t list 2>&1
[[ "$module_name" == "$(/usr/bin/python3 "$probe_control/probe_module_publication.py" "$probe_root" next)" ]]
sai_module unload "$module_name"
[[ -z "${SAI_CP2K_IMAGE:-}" && "$PATH" != *"$probe_root/controller/cpu-first"* ]]
sai_module load "$module_name"
[[ "$SAI_CP2K_IMAGE" == "$probe_root/containers/software/cp2k/development/$build_id/DSPRHBM/cpu-next.sif" ]]
[[ "$(command -v cp2k)" == "$probe_root/controller/cpu-next/cp2k" ]]
sai_module unload "$module_name"
sai_module show "$module_name"
[[ -z "${SAI_CP2K_IMAGE:-}" ]]
echo REAL_LMOD_PARTITION_PAIR_AND_RELOAD_VERIFIED
