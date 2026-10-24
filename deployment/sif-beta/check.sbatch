#!/usr/bin/env bash
# Module integration smoke check; scientific acceptance belongs to the fixed SIF.
#SBATCH --time=00:10:00
#SBATCH --export=ALL
set -eo pipefail
module_root=${1:?gateway module root}
output=${2:?new project check directory}
[[ $SLURM_JOB_ACCOUNT == proj-sai-hpc-software && $output == /org/org-open-sai/proj-sai-hpc-software/* ]]
source /etc/profile.d/lmod.sh
umask 077
export TMPDIR="$output/tmp"
mkdir -p "$TMPDIR"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export SLURM_EXPORT_ENV=ALL
export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher
export SAI_SIF_TRACE_DIR="$output/trace"
mkdir -m 0700 -p "$SAI_SIF_TRACE_DIR"
cd "$output"
scontrol show job -o "$SLURM_JOB_ID" > allocation.txt
printf '%s\n' "$SLURM_JOB_ID" > job.id
for name in cp2k abacus lammps deepmd-kit gpumd; do
  module purge
  module use "$module_root"
  branch=master
  [[ $name != abacus && $name != lammps ]] || branch=develop
  module load "$name/$branch-beta-later"
  key=SAI_SIF_${name^^}
  key=${key//-/_}
  image_key=${key}_IMAGE
  templates_key=${key}_TEMPLATES
  generation_key=${key}_BETA_GENERATION
  [[ -r ${!image_key} && -r ${!templates_key}/gpu.sbatch && -n ${!generation_key} ]]
  env | sort | awk '/^SAI_SIF_/' > "$name-environment.txt"
  module -t list > "$name-modules.txt" 2>&1
  case $name in
    cp2k) mpirun -np 2 --map-by ppr:2:node:PE=1 --bind-to core cp2k.psmp --version > cp2k-version.txt 2>&1 ;;
    abacus) command -v abacus > abacus-command.txt ;;
    lammps) mpirun -np 2 --map-by ppr:2:node:PE=1 --bind-to core lmp -h > lammps-version.txt 2>&1 ;;
    deepmd-kit) dp --version > deepmd-kit-version.txt 2>&1 ;;
    gpumd) command -v gpumd nep gnep > gpumd-commands.txt ;;
  esac
  module unload "$name/$branch-beta-later"
  [[ -z ${!generation_key} && -z ${!image_key} && -z ${!templates_key} ]]
done
module purge
printf '%s\n' 'All five beta modules loaded and unloaded; CP2K/LAMMPS/DeePMD startup passed.' > passed.txt
