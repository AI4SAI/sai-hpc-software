#!/usr/bin/env bash
# Run ID must name a fresh independent GPUMD task directory.
#SBATCH --job-name=gpumd-site-probe
#SBATCH --partition=4V100
#SBATCH --qos=flood-1o2gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --time=5
#SBATCH --export=NIL
set -eo pipefail
task=${1:?GPUMD task required}
control=${2:?trusted controller snapshot required}
[[ "$task" == /home/stardust/sai-hpc-software/runs/gpumd-site-probe-* ]]
[[ "$control" == /home/stardust/sai-hpc-software/controller/gpumd-site-probe-* ]]
export PATH=/usr/bin:/bin LD_LIBRARY_PATH= LD_PRELOAD=
source /etc/profile.d/lmod.sh
module load apptainer/1.4.4
set -u
source /opt/sai_config/mps_mapping.d/4V100.bash
export TMPDIR="$task/runtime" APPTAINER_TMPDIR="$task/runtime" APPTAINER_CACHEDIR="$task/cache"
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
apptainer exec --nv --cleanenv --containall --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd /work \
  --bind /usr:/usr:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro \
  --bind /opt/devtools:/opt/devtools:ro --bind /opt/apps:/opt/apps:ro \
  --bind /opt/modules:/opt/modules:ro --bind /etc/profile.d/lmod.sh:/etc/profile.d/lmod.sh:ro \
  --bind "$control:/control:ro" --bind "$task/results:/work:rw" --bind "$task/runtime:/runtime:rw" \
  --env TMPDIR=/runtime --env "SLURM_JOB_ID=$SLURM_JOB_ID" --env "CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES" \
  /home/stardust/sai-hpc-software/containers/base/minimal-v1.sif /usr/bin/bash -c \
  'driver=${LD_LIBRARY_PATH:-}; source /control/gpumd_environment.sh 4v100-avx512; export LD_LIBRARY_PATH="$driver:$LD_LIBRARY_PATH"; export GPUMD_SRC=/opt/apps/gpumd/GPUMD-5.8/src; export CUDACXX="$CUDA_HOME/bin/nvcc"; python3 /control/gpumd_site_probe.py'
