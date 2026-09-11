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
probe_root=/home/stardust/sai-hpc-software/experimental/module-publication-20260911-v2
probe_control=/home/stardust/sai-hpc-software/controller/module-publication-probe-20260911-v2
# Site Lmod intentionally unsets the last LD_LIBRARY_PATH entry on unload;
# its shell wrapper is not nounset-safe when a later module is loaded.
sai_module() {
  set +u
  module "$@"
  module_status=$?
  set -u
  return "$module_status"
}
/usr/bin/python3 "$probe_control/probe_module_publication.py" "$probe_root" initial
sai_module use "$probe_root/modulefiles/apps"
sai_module load cp2k/probe
[[ "$SAI_CP2K_IMAGE" == "$probe_root/containers/software/cp2k/probe/dsprhbm/cpu-first.sif" ]]
[[ "$(command -v cp2k)" == "$probe_root/controller/cpu-first/cp2k" ]]
sai_module -t list 2>&1
/usr/bin/python3 "$probe_control/probe_module_publication.py" "$probe_root" next
sai_module unload cp2k/probe
[[ -z "${SAI_CP2K_IMAGE:-}" && "$PATH" != *"$probe_root/controller/cpu-first"* ]]
sai_module load cp2k/probe
[[ "$SAI_CP2K_IMAGE" == "$probe_root/containers/software/cp2k/probe/dsprhbm/cpu-next.sif" ]]
[[ "$(command -v cp2k)" == "$probe_root/controller/cpu-next/cp2k" ]]
sai_module unload cp2k/probe
sai_module show cp2k/probe
[[ -z "${SAI_CP2K_IMAGE:-}" ]]
echo REAL_LMOD_PARTITION_PAIR_AND_RELOAD_VERIFIED
