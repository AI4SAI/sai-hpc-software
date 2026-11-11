#!/bin/bash
#SBATCH --job-name=gpumdkit-install-check
#SBATCH --partition=16V100
#SBATCH --qos=flood-1o2gpu
#SBATCH --account=proj-sai-hpc-software
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --time=00:10:00
#SBATCH --export=NIL
#SBATCH --chdir=/org/org-open-sai/proj-sai-hpc-software/operations/gpumdkit-install-permissions-20261112/native-stage
#SBATCH --output=/org/org-open-sai/proj-sai-hpc-software/operations/gpumdkit-install-permissions-20261112/slurm-%j.log
set -eo pipefail
export PATH=/usr/bin:/bin
unset PYTHONHOME PYTHONPATH LD_LIBRARY_PATH LD_PRELOAD
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
source /etc/profile.d/lmod.sh
module purge
module use "$PWD/opt/modules/modulefiles/apps"
module load gpumdkit/1.5.9
prefix="$PWD/opt/apps/GPUMDkit/1.5.9"
[[ $(command -v gpumdkit) == "$prefix/bin/gpumdkit" ]]
[[ $SAI_PYTHON_ENV == "$prefix" ]]
[[ -z ${LD_LIBRARY_PATH:-} ]]
module --ignore_cache whatis gpumdkit/1.5.9
export SAI_GPUMDKIT_ROUTE=staged-native-module-permission-fix
export SAI_GPUMDKIT_IMAGE_SHA256=1c0424c49a39c3b3e7928ba8b5863ec32c87a3d92dde3e8d3886d1c3ab137df2
export TMPDIR="$PWD/tmp-$SLURM_JOB_ID"
mkdir "$TMPDIR"
"$prefix/bin/python" -B /home/stardust/sai-hpc-software/deployment/gpumdkit-1.5.9/accept.py "$prefix" \
    /org/org-open-sai/proj-sai-hpc-software/experimental/gpumdkit/runs/v1.5.9-20261111-c/control/fixtures \
    "$PWD/acceptance-$SLURM_JOB_ID"
module unload gpumdkit/1.5.9
[[ -z ${GPUMDkit_path:-} && -z ${SAI_PYTHON_ENV:-} ]]
printf 'GPUMDKIT_NATIVE_MODULE_ACCEPTED\n'
