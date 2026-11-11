#!/bin/bash
#SBATCH --job-name=gpumdkit-1.5.9
#SBATCH --partition=16V100
#SBATCH --qos=flood-1o2gpu
#SBATCH --account=proj-sai-hpc-software
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --time=00:30:00
#SBATCH --export=NIL
#SBATCH --chdir=/org/org-open-sai/proj-sai-hpc-software/experimental/gpumdkit/runs/v1.5.9-20261111-b
#SBATCH --output=/org/org-open-sai/proj-sai-hpc-software/experimental/gpumdkit/runs/v1.5.9-20261111-b/slurm-%j.log
set -euo pipefail
export HOME=/home/stardust USER=stardust LOGNAME=stardust
export PATH=/usr/bin:/bin LD_LIBRARY_PATH='' PYTHONPATH='' LD_PRELOAD=''
source /etc/profile.d/lmod.sh
module load apptainer/1.4.4
run=$PWD
cache=/org/org-open-sai/proj-sai-hpc-software/experimental/gpumdkit/cache
seed=/org/org-open-sai/proj-sai-hpc-software/cache/python-envs-20261104-a
export TMPDIR="$run/runtime"
export APPTAINER_TMPDIR="$run/runtime/apptainer-tmp" APPTAINER_CACHEDIR="$run/runtime/apptainer-cache"
mkdir -p "$run/prefix" "$APPTAINER_TMPDIR" "$APPTAINER_CACHEDIR"
apptainer exec --containall --cleanenv --no-home --no-mount tmp \
    --bind /usr:/usr:ro,/lib:/lib:ro,/lib64:/lib64:ro,/sbin:/sbin:ro,/opt/devtools:/opt/devtools:ro \
    --bind "$run/prefix:/opt/apps/GPUMDkit/1.5.9:rw" \
    --bind "$run/runtime:/runtime:rw,$run/control:/control:ro" \
    --bind "$seed/conda:/input/packages/conda:ro,$seed/python-explicit.txt:/input/python-explicit.txt:ro" \
    --bind "$cache/wheels:/input/wheels:ro,$cache/source/source.tar.gz:/input/source.tar.gz:ro" \
    /org/org-open-sai/proj-sai-hpc-software/containers/base/minimal-v1.sif \
    /usr/bin/bash /control/build.sh
python3 "$run/control/pack.py" "$run"
apptainer sif new "$run/gpumdkit-1.5.9.sif"
apptainer sif add --datatype 4 --partfs 1 --parttype 2 --partarch 2 --groupid 1 \
    "$run/gpumdkit-1.5.9.sif" "$run/rootfs.squashfs"
apptainer exec --containall --cleanenv --no-home --no-mount tmp \
    --bind /usr:/usr:ro,/lib:/lib:ro,/lib64:/lib64:ro,/sbin:/sbin:ro \
    --bind "$run/runtime:/runtime:rw,$run/control:/control:ro" \
    --env TMPDIR=/runtime/tmp,PATH=/opt/apps/GPUMDkit/1.5.9/bin:/usr/bin:/bin,LD_LIBRARY_PATH=/opt/apps/GPUMDkit/1.5.9/lib \
    --env SAI_GPUMDKIT_ROUTE=sif,OMP_NUM_THREADS=1,OPENBLAS_NUM_THREADS=1,MKL_NUM_THREADS=1 \
    "$run/gpumdkit-1.5.9.sif" \
    /opt/apps/GPUMDkit/1.5.9/bin/python -B /control/accept.py \
    /opt/apps/GPUMDkit/1.5.9 /control/fixtures /runtime/sif-acceptance
sha256sum "$run/gpumdkit-1.5.9.sif" > "$run/artifact.sha256"
chmod 444 "$run/gpumdkit-1.5.9.sif"
printf 'GPUMDKIT_BUILD_COMPLETED\n'
