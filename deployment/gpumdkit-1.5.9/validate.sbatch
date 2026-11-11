#!/bin/bash
#SBATCH --job-name=gpumdkit-validate
#SBATCH --partition=16V100
#SBATCH --qos=flood-1o2gpu
#SBATCH --account=proj-sai-hpc-software
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --time=00:15:00
#SBATCH --export=NIL
#SBATCH --chdir=/org/org-open-sai/proj-sai-hpc-software/experimental/gpumdkit/runs/v1.5.9-20261111-c
#SBATCH --output=/org/org-open-sai/proj-sai-hpc-software/experimental/gpumdkit/runs/v1.5.9-20261111-c/validate-%j.log
set -euo pipefail
export HOME=/home/stardust USER=stardust LOGNAME=stardust
export PATH=/usr/bin:/bin LD_LIBRARY_PATH='' PYTHONPATH='' LD_PRELOAD=''
source /etc/profile.d/lmod.sh
module load apptainer/1.4.4
run=$PWD
if [[ ! -f "$run/gpumdkit-1.5.9.sif" ]]; then
    python3 "$run/validation-control/pack.py" "$run" /org/org-open-sai/proj-sai-hpc-software/experimental/gpumdkit/runs/v1.5.9-20261111-b
    apptainer sif new "$run/gpumdkit-1.5.9.sif"
    apptainer sif add --datatype 4 --partfs 1 --parttype 2 --partarch 2 --groupid 1 "$run/gpumdkit-1.5.9.sif" "$run/rootfs.squashfs"
fi
output="$run/validation-$SLURM_JOB_ID"
mkdir "$output"
cp -a "$run/validation-control" "$output/control"
mkdir "$output/runtime"
export TMPDIR="$output/runtime"
export APPTAINER_TMPDIR="$output/runtime/apptainer-tmp" APPTAINER_CACHEDIR="$output/runtime/apptainer-cache"
mkdir "$APPTAINER_TMPDIR" "$APPTAINER_CACHEDIR"
export SAI_GPUMDKIT_IMAGE_SHA256
SAI_GPUMDKIT_IMAGE_SHA256=$(sha256sum "$run/gpumdkit-1.5.9.sif" | cut -d ' ' -f 1)
apptainer exec --containall --cleanenv --no-home --no-mount tmp --pwd /runtime \
    --bind /usr:/usr:ro,/lib:/lib:ro,/lib64:/lib64:ro,/sbin:/sbin:ro \
    --bind "$output/runtime:/runtime:rw,$output/control:/control:ro" \
    --bind "$run/control/fixtures:/fixtures:ro" \
    --env TMPDIR=/runtime,PATH=/opt/apps/GPUMDkit/1.5.9/bin:/usr/bin:/bin,LD_LIBRARY_PATH= \
    --env SAI_GPUMDKIT_ROUTE=sif,OMP_NUM_THREADS=1,OPENBLAS_NUM_THREADS=1,MKL_NUM_THREADS=1 \
    --env "SAI_GPUMDKIT_IMAGE_SHA256=$SAI_GPUMDKIT_IMAGE_SHA256,SLURM_JOB_ID=$SLURM_JOB_ID" \
    "$run/gpumdkit-1.5.9.sif" \
    /opt/apps/GPUMDkit/1.5.9/bin/python -B /control/accept.py \
    /opt/apps/GPUMDkit/1.5.9 /fixtures /runtime/sif-acceptance
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export SAI_GPUMDKIT_ROUTE=staged-native
python3 -B - "$run" "$output" <<'PY'
import sys,json
from pathlib import Path
run,output=map(Path,sys.argv[1:])
sys.path.insert(0,str(output/'control'))
import install
records=json.loads((run/'inventory.json').read_text())
install.validate_inventory(records)
install.verify(run/'prefix',records)
PY
"$run/prefix/bin/python" -B "$output/control/accept.py" "$run/prefix" "$run/control/fixtures" "$output/runtime/native-acceptance"
printf 'GPUMDKIT_SIF_AND_NATIVE_ACCEPTED\n'
