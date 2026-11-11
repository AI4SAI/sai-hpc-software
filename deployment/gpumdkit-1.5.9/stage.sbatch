#!/bin/bash
#SBATCH --job-name=gpumdkit-native-module
#SBATCH --partition=16V100
#SBATCH --qos=flood-1o2gpu
#SBATCH --account=proj-sai-hpc-software
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --time=00:10:00
#SBATCH --export=NIL
#SBATCH --chdir=/org/org-open-sai/proj-sai-hpc-software/operations/gpumdkit-native-stage-20261111
#SBATCH --output=/org/org-open-sai/proj-sai-hpc-software/operations/gpumdkit-native-stage-20261111/slurm-%j.log
set -euo pipefail
export HOME=/home/stardust USER=stardust LOGNAME=stardust
export PATH=/usr/bin:/bin LD_LIBRARY_PATH='' PYTHONPATH='' LD_PRELOAD=''
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
source /etc/profile.d/lmod.sh
module purge
module use "$PWD/opt/modules/modulefiles/apps"
module load gpumdkit/1.5.9
prefix="$PWD/opt/apps/GPUMDkit/1.5.9"
[[ $(command -v gpumdkit) == "$prefix/bin/gpumdkit" ]]
[[ -z "$LD_LIBRARY_PATH" ]]
export SAI_GPUMDKIT_ROUTE=staged-native-module
export SAI_GPUMDKIT_IMAGE_SHA256=1c0424c49a39c3b3e7928ba8b5863ec32c87a3d92dde3e8d3886d1c3ab137df2
export TMPDIR="$PWD/tmp-$SLURM_JOB_ID"
mkdir "$TMPDIR"
"$prefix/bin/python" -B /home/stardust/sai-hpc-software/deployment/gpumdkit-1.5.9/accept.py "$prefix" \
    /org/org-open-sai/proj-sai-hpc-software/experimental/gpumdkit/runs/v1.5.9-20261111-c/control/fixtures \
    "$PWD/acceptance-$SLURM_JOB_ID"
module unload gpumdkit/1.5.9
[[ -z "${GPUMDkit_path:-}" ]]
printf 'GPUMDKIT_NATIVE_MODULE_ACCEPTED\n'
