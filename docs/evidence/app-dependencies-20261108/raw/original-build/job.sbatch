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
#SBATCH --output=/org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1/results/slurm-%j.log
#SBATCH --account=proj-sai-hpc-software
#SBATCH --chdir=/org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1
set -eo pipefail
export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""
source /etc/profile.d/lmod.sh
module load apptainer/1.4.4
set -u
umask 077
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
export TMPDIR=/org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1/runtime
export APPTAINER_TMPDIR="$TMPDIR"
export APPTAINER_CACHEDIR=/org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1/apptainer-cache
test ! -e /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1/work.ext3
apptainer overlay create --fakeroot --sparse --size 8192 /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1/work.ext3
/usr/bin/python3 /org/org-open-sai/proj-sai-hpc-software/controller/app-dependencies-20261108-r1/cp2k_image_driver.py /org/org-open-sai/proj-sai-hpc-software /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1 exec --ignore-fakeroot-command --bind /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1/results:/results:rw --env SLURM_JOB_PARTITION=DSPRHBM --fakeroot --cleanenv --containall --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd / --net --network none --bind /usr:/usr:ro --bind /sbin:/sbin:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro --bind /opt/devtools:/opt/devtools:ro --bind /opt/modules:/opt/modules:ro --bind /opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/comm_libs/12.9/hpcx/hpcx-2.25.1/hcoll:/opt/mellanox/hcoll:ro --bind /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1/input/deepks.ptg:/input/deepks.ptg:ro --bind /org/org-open-sai/proj-sai-hpc-software/runs/42437630318-1-development-dsprhbm-2026-11-08-61569eb48023/input/qe-dependencies:/input/qe-dependencies:ro --bind /opt/apps/abacus/abacus-develop-3.9.0.26_nvhpc263gnu/toolchain/build:/input/abacus-dependencies:ro --bind /etc/profile.d/lmod.sh:/etc/profile.d/lmod.sh:ro --bind /etc/lmod:/etc/lmod:ro --bind /org/org-open-sai/proj-sai-hpc-software/controller/app-dependencies-20261108-r1:/control:ro --overlay /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1/work.ext3 --env BUILD_JOBS=8 --env TMPDIR=/workspace/tmp /org/org-open-sai/proj-sai-hpc-software/containers/base/minimal-v1.sif /usr/bin/bash /control/app_dependency_entry.sh build
/usr/sbin/e2fsck -fn /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1/work.ext3 > /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1/results/fsck.log 2>&1
/usr/bin/python3 /org/org-open-sai/proj-sai-hpc-software/controller/app-dependencies-20261108-r1/cp2k_image_driver.py /org/org-open-sai/proj-sai-hpc-software /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1 exec --ignore-fakeroot-command --fakeroot --cleanenv --containall --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd / --net --network none --bind /usr:/usr:ro --bind /sbin:/sbin:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro --bind /opt/devtools:/opt/devtools:ro --bind /opt/modules:/opt/modules:ro --bind /etc/profile.d/lmod.sh:/etc/profile.d/lmod.sh:ro --bind /etc/lmod:/etc/lmod:ro --overlay /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1/work.ext3:ro --env BUILD_JOBS=8 --env TMPDIR=/workspace/tmp /org/org-open-sai/proj-sai-hpc-software/containers/base/minimal-v1.sif /usr/bin/cat /workspace/final.squashfs > /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1/libraries.squashfs
apptainer sif new /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1/libraries.sif
apptainer sif add --datatype 4 --partfs 1 --parttype 2 --partarch 2 --groupid 1 /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1/libraries.sif /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1/libraries.squashfs
apptainer exec --ignore-fakeroot-command --bind /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1/results:/results:rw --env SLURM_JOB_PARTITION=DSPRHBM --fakeroot --cleanenv --containall --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd / --net --network none --bind /usr:/usr:ro --bind /sbin:/sbin:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro --bind /opt/devtools:/opt/devtools:ro --bind /opt/modules:/opt/modules:ro --bind /opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/comm_libs/12.9/hpcx/hpcx-2.25.1/hcoll:/opt/mellanox/hcoll:ro --bind /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1/input/deepks.ptg:/input/deepks.ptg:ro --bind /etc/profile.d/lmod.sh:/etc/profile.d/lmod.sh:ro --bind /etc/lmod:/etc/lmod:ro --bind /org/org-open-sai/proj-sai-hpc-software/controller/app-dependencies-20261108-r1:/control:ro --bind /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1/runtime:/runtime:rw --env BUILD_JOBS=8 --env TMPDIR=/runtime /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1/libraries.sif /usr/bin/bash /control/app_dependency_entry.sh verify
sha256sum /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1/libraries.sif > /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1/results/artifact.sha256
chmod 444 /org/org-open-sai/proj-sai-hpc-software/runs/app-dependencies-20261108-r1/libraries.sif
echo APPLICATION_DEPENDENCY_CONSUMERS_PASSED
