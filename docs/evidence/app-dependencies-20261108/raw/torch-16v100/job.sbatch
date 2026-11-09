#!/bin/bash
#SBATCH --job-name=libtorch-consumer-16V100
#SBATCH --partition=16V100
#SBATCH --qos=flood-1o2gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --time=10
#SBATCH --account=proj-sai-hpc-software
#SBATCH --export=NIL
#SBATCH --chdir=/org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-torch-16v100-20261108-r1
#SBATCH --output=/org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-torch-16v100-20261108-r1/results/slurm-%j.log
set -eo pipefail
export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""
source /etc/profile.d/lmod.sh
module load apptainer/1.4.4
set -u
umask 077
export TMPDIR=/org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-torch-16v100-20261108-r1/runtime
export APPTAINER_TMPDIR="$TMPDIR"
export APPTAINER_CACHEDIR=/org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-torch-16v100-20261108-r1/apptainer-cache
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
apptainer exec --ignore-fakeroot-command --bind /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-torch-16v100-20261108-r1/results:/results:rw --env SLURM_JOB_PARTITION=16V100 --fakeroot --cleanenv --containall --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd / --net --network none --bind /usr:/usr:ro --bind /sbin:/sbin:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro --bind /opt/devtools:/opt/devtools:ro --bind /opt/modules:/opt/modules:ro --bind /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r2/input/deepks.ptg:/input/deepks.ptg:ro --bind /etc/profile.d/lmod.sh:/etc/profile.d/lmod.sh:ro --bind /etc/lmod:/etc/lmod:ro --bind /org/org-open-sai/proj-sai-hpc-software/controller/app-dependencies-20261108-r2:/control:ro --bind /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-torch-16v100-20261108-r1/runtime:/runtime:rw --env BUILD_JOBS=2 --env TMPDIR=/runtime /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r2/libraries.sif /usr/bin/bash /control/app_dependency_entry.sh verify-torch 16v100-avx2
