#!/bin/bash
#SBATCH --job-name=sai-cp2k-deps-4v100-avx512-aed7aa53ff43
#SBATCH --dependency=singleton
#SBATCH --partition=4V100
#SBATCH --qos=flood-1o2gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=240
#SBATCH --export=NIL
#SBATCH --output=/home/stardust/sai-hpc-software/runs/39365139034-1-release-4v100-avx512-2026-10-20-67b5da876dd6-dependencies/results/slurm-%j.log
#SBATCH --gpus-per-node=1
set -eo pipefail
export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""
source /etc/profile.d/lmod.sh
module load apptainer/1.4.4
set -u
umask 077
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
export TMPDIR=/home/stardust/sai-hpc-software/runs/39365139034-1-release-4v100-avx512-2026-10-20-67b5da876dd6-dependencies/runtime
export APPTAINER_TMPDIR="$TMPDIR"
export APPTAINER_CACHEDIR=/home/stardust/sai-hpc-software/runs/39365139034-1-release-4v100-avx512-2026-10-20-67b5da876dd6-dependencies/apptainer-cache
/usr/bin/python3 /home/stardust/sai-hpc-software/controller/13e1a7ef3cd218c96c9635019b34ccc5c4d624ce/39365139034-1-release-4v100-avx512-2026-10-20-67b5da876dd6/build_capacity.py --shell-script-stdin /home/stardust/sai-hpc-software /home/stardust/sai-hpc-software/runs/39365139034-1-release-4v100-avx512-2026-10-20-67b5da876dd6-dependencies 16384 <<'SAI_BUILD_BODY'
/usr/bin/python3 -c 'import sys; sys.path.insert(0, '"'"'/home/stardust/sai-hpc-software/controller/13e1a7ef3cd218c96c9635019b34ccc5c4d624ce/39365139034-1-release-4v100-avx512-2026-10-20-67b5da876dd6'"'"'); from remote_controller import require_capacity; require_capacity('"'"'/home/stardust/sai-hpc-software'"'"', 16384)'
test ! -e /home/stardust/sai-hpc-software/runs/39365139034-1-release-4v100-avx512-2026-10-20-67b5da876dd6-dependencies/work.ext3
cp --sparse=always --reflink=auto -- /home/stardust/sai-hpc-software/cache/spack/bootstrap/spack-source-v2.ext3 /home/stardust/sai-hpc-software/runs/39365139034-1-release-4v100-avx512-2026-10-20-67b5da876dd6-dependencies/work.ext3
chmod 600 /home/stardust/sai-hpc-software/runs/39365139034-1-release-4v100-avx512-2026-10-20-67b5da876dd6-dependencies/work.ext3
printf '%s  %s
' 80497b787dc178876e71098840cf465deb63c6c9bec27c9dd5cc0bed3643c337 /home/stardust/sai-hpc-software/runs/39365139034-1-release-4v100-avx512-2026-10-20-67b5da876dd6-dependencies/work.ext3 | sha256sum --check --status
/usr/sbin/e2fsck -pf /home/stardust/sai-hpc-software/runs/39365139034-1-release-4v100-avx512-2026-10-20-67b5da876dd6-dependencies/work.ext3 || test "$?" -eq 1
/usr/sbin/resize2fs /home/stardust/sai-hpc-software/runs/39365139034-1-release-4v100-avx512-2026-10-20-67b5da876dd6-dependencies/work.ext3 16384M
/usr/bin/python3 /home/stardust/sai-hpc-software/controller/13e1a7ef3cd218c96c9635019b34ccc5c4d624ce/39365139034-1-release-4v100-avx512-2026-10-20-67b5da876dd6/cp2k_image_driver.py /home/stardust/sai-hpc-software /home/stardust/sai-hpc-software/runs/39365139034-1-release-4v100-avx512-2026-10-20-67b5da876dd6-dependencies exec --bind /home/stardust/sai-hpc-software/runs/39365139034-1-release-4v100-avx512-2026-10-20-67b5da876dd6-dependencies/results:/results:rw --fakeroot --cleanenv --containall --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd / --net --network none --nv --bind /usr:/usr:ro --bind /sbin:/sbin:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro --bind /opt/devtools:/opt/devtools:ro --bind /opt/modules:/opt/modules:ro --bind /home/stardust/sai-hpc-software/cache/spack:/input/spack:ro --bind /var/lib/dpkg:/var/lib/dpkg:ro --bind /usr/lib/os-release:/etc/os-release:ro --bind /etc/alternatives:/etc/alternatives:ro --bind /usr/bin:/bin:ro --bind /etc/profile.d/lmod.sh:/etc/profile.d/lmod.sh:ro --bind /etc/lmod:/etc/lmod:ro --bind /home/stardust/sai-hpc-software/controller/13e1a7ef3cd218c96c9635019b34ccc5c4d624ce/39365139034-1-release-4v100-avx512-2026-10-20-67b5da876dd6:/control:ro --overlay /home/stardust/sai-hpc-software/runs/39365139034-1-release-4v100-avx512-2026-10-20-67b5da876dd6-dependencies/work.ext3 --env BUILD_JOBS=8 --env TMPDIR=/workspace/tmp /home/stardust/sai-hpc-software/containers/base/minimal-v1.sif /usr/bin/env SAI_BUILD_PARTITION=4V100 /bin/bash /control/cp2k_container_entry.sh dependencies cp2k 67b5da876dd6a76b8b021d5a04d1c81ba79a4c50 v2026.2 4v100-avx512 '{"build_id":"v2026.2-g67b5da876dd6-raed7aa53ff43","cpu_arch":"znver4","cuda_arch":"70","dependency_isa":"avx512","gpus":1,"install_prefix":"/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/4V100","partition":"4V100","recipe_sha256":"aed7aa53ff436310f28e5fdc21e1c3ca211f2422c5dd6abe44b19aa1d3ae9277","repository":"cp2k/cp2k","schema":2,"software":"cp2k","source_ref":"v2026.2","source_sha":"67b5da876dd6a76b8b021d5a04d1c81ba79a4c50","source_version":"v2026.2","stack_digest":null,"stack_sources":{},"target":"4v100-avx512","track":"release","version_label":"v2026.2"}'
echo CP2K_DEPENDENCY_STAGE_COMPLETE_NOT_APPLICATION_ACCEPTANCE
SAI_BUILD_BODY
