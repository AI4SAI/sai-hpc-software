#!/bin/bash
#SBATCH --job-name=sai-app-dependencies
#SBATCH --partition=DSPRHBM
#SBATCH --qos=rush-cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=28G
#SBATCH --time=30
#SBATCH --export=NIL
#SBATCH --output=/org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r2/results/slurm-%j.log
#SBATCH --account=proj-sai-hpc-software
#SBATCH --chdir=/org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r2
set -eo pipefail
export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""
source /etc/profile.d/lmod.sh
module load apptainer/1.4.4
set -u
umask 077
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
export TMPDIR=/org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r2/runtime
export APPTAINER_TMPDIR="$TMPDIR"
export APPTAINER_CACHEDIR=/org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r2/apptainer-cache
apptainer exec --bind /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r2/runtime:/root/.config:rw --ignore-fakeroot-command --bind /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r2/results:/results:rw --env SLURM_JOB_PARTITION=DSPRHBM --fakeroot --cleanenv --containall --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd / --net --network none --bind /usr:/usr:ro --bind /sbin:/sbin:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro --bind /opt/devtools:/opt/devtools:ro --bind /opt/modules:/opt/modules:ro --bind /opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/comm_libs/12.9/hpcx/hpcx-2.25.1/hcoll:/opt/mellanox/hcoll:ro --bind /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r2/input/deepks.ptg:/input/deepks.ptg:ro --bind /etc/profile.d/lmod.sh:/etc/profile.d/lmod.sh:ro --bind /etc/lmod:/etc/lmod:ro --bind /org/org-open-sai/proj-sai-hpc-software/controller/app-dependencies-20261108-r2:/control:ro --bind /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r2/runtime:/runtime:rw --env BUILD_JOBS=8 --env TMPDIR=/runtime /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r2/libraries.sif /usr/bin/bash /control/app_dependency_entry.sh verify
sha256sum /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r2/libraries.sif > /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r2/results/artifact.sha256
echo APPLICATION_DEPENDENCY_CONSUMERS_PASSED
