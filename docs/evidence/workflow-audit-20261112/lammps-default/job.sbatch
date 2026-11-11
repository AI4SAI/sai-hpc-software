#!/bin/bash
#SBATCH --job-name=lammps-default-load
#SBATCH --partition=16V100
#SBATCH --qos=flood-1o2gpu
#SBATCH --account=proj-sai-hpc-software
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --time=00:10:00
#SBATCH --export=NIL
#SBATCH --chdir=/org/org-open-sai/proj-sai-hpc-software/operations/lammps-default-load-20261112-retry1
#SBATCH --output=/org/org-open-sai/proj-sai-hpc-software/operations/lammps-default-load-20261112-retry1/slurm-%j.log
set -eo pipefail
export HOME=/home/stardust USER=stardust LOGNAME=stardust
export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""
export TMPDIR="$PWD/runtime" TMP="$PWD/runtime" TEMP="$PWD/runtime"
export PYTHONDONTWRITEBYTECODE=1
source /etc/profile.d/lmod.sh
module purge
module use "$PWD/stage/opt/modules/modulefiles/apps"
module --config > lmod-config.txt 2>&1
module load lammps
module -t list > lammps-module-list.txt 2>&1
env | sort | grep -E '^(LOADEDMODULES|_LMFILES_|SAI_LAMMPS_PREFIX|LMOD_)=' > lammps-selection.txt
[[ "$SAI_LAMMPS_PREFIX" == /opt/apps/lammps/lammps-release-stable_30Sep2026_update1-g098ffdefa525-rb6a6aadddb8b/16V100 ]]
module unload lammps
[[ -z "${SAI_LAMMPS_PREFIX:-}" ]]
module load nepadapters
module -t list > nepadapters-module-list.txt 2>&1
env | sort | grep -E '^(LOADEDMODULES|_LMFILES_|SAI_LAMMPS_PREFIX|SAI_NEPADAPTERS_PREFIX|LAMMPS_PLUGIN_PATH|LMOD_)=' > nepadapters-selection.txt
[[ "$SAI_LAMMPS_PREFIX" == /opt/apps/lammps/lammps-release-stable_30Sep2026_update1-g098ffdefa525-rb6a6aadddb8b/16V100 ]]
[[ "$SAI_NEPADAPTERS_PREFIX" == /opt/apps/nepadapters/1.0.5-lammps30Sep2026 ]]
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 SLURM_EXPORT_ENV=ALL
export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher
/usr/bin/python3 -B single-gpu.py
module unload nepadapters
[[ -z "${SAI_NEPADAPTERS_PREFIX:-}" && -z "${SAI_LAMMPS_PREFIX:-}" ]]
touch module-unload.passed
