#!/usr/bin/env bash
# Diagnostic reuse of a terminated build overlay; NEVER publishes a catalog SIF.
#SBATCH --job-name=gpumd-relink-diagnostic
#SBATCH --partition=4V100
#SBATCH --qos=flood-1o2gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --time=40
#SBATCH --export=NIL
set -eo pipefail
task=${1:?fresh GPUMD diagnostic task}
control=${2:?fresh trusted GPUMD diagnostic snapshot}
old=${3:?terminated GPUMD build overlay}
sha=${4:?source SHA}
version=${5:?version label}
[[ "$task" == /home/stardust/sai-hpc-software/runs/gpumd-relink-* ]]
[[ "$control" == /home/stardust/sai-hpc-software/controller/gpumd-relink-* ]]
[[ "$old" == /home/stardust/sai-hpc-software/runs/gpumd-*/work.ext3 && -f "$old" && ! -L "$old" ]]
[[ "$sha" =~ ^[0-9a-f]{40}$ && "$version" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]
export PATH=/usr/bin:/bin LD_LIBRARY_PATH= LD_PRELOAD=
source /etc/profile.d/lmod.sh
module load apptainer/1.4.4
set -u
source /opt/sai_config/mps_mapping.d/4V100.bash
export TMPDIR="$task/runtime" APPTAINER_TMPDIR="$task/runtime" APPTAINER_CACHEDIR="$task/cache"
export APPTAINERENV_CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:?}"
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
test ! -e "$task/work.ext3"
cp --reflink=auto --sparse=always "$old" "$task/work.ext3"
args=(apptainer exec --fakeroot --nv --cleanenv --containall --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd /
      --net --network none --overlay "$task/work.ext3"
      --bind /usr:/usr:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro
      --bind /opt/devtools:/opt/devtools:ro --bind /opt/apps:/opt/apps:ro
      --bind /opt/modules:/opt/modules:ro --bind /etc/profile.d/lmod.sh:/etc/profile.d/lmod.sh:ro
      --bind "$control:/control:ro"
      --bind /home/stardust/sai-hpc-software/cache/repositories/gpumd:/input/repository:ro
      --bind "$task/results:/diagnostics:rw" --env BUILD_JOBS=8 --env TMPDIR=/workspace/tmp
      --env "SLURM_JOB_ID=$SLURM_JOB_ID")
base=/home/stardust/sai-hpc-software/containers/base/minimal-v1.sif
"${args[@]}" "$base" /usr/bin/bash /control/gpumd_container_entry.sh build gpumd "$sha" "$version" 4v100-avx512
nvidia-smi --query-gpu=name,uuid,driver_version --format=csv,noheader > "$task/results/gpu-host.txt"
"${args[@]}" "$base" /usr/bin/bash -c \
  'driver=${LD_LIBRARY_PATH:-}; source "$1/share/sai/runtime-env.sh"; export LD_LIBRARY_PATH="$driver:$LD_LIBRARY_PATH"; /usr/bin/python3 /control/gpumd_science.py run "$1" /diagnostics/science' \
  bash "/opt/software/gpumd/$version/4v100-avx512"
echo GPUMD_OVERLAY_DIAGNOSTIC_ONLY_NO_PUBLICATION
