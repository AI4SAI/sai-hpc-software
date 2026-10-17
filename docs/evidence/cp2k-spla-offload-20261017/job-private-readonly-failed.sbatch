#!/bin/bash
#SBATCH --job-name=sai-spla-offload-probe
#SBATCH --partition=4V100
#SBATCH --qos=flood-1o2gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --time=20
#SBATCH --export=NIL
#SBATCH --output=/home/stardust/sai-hpc-software/runs/cp2k-spla-probe-20261017-a/results/slurm-%j.log
set -euo pipefail
export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""
umask 077
project=/home/stardust/sai-hpc-software
probe="$project/runs/cp2k-spla-probe-20261017-a"
control="$project/controller/1e7d736e5884fe207460a3e6baf0aeddb5bdb4aa/39032306213-1-release-4v100-avx512-2026-10-17-67b5da876dd6"
sealed="$project/runs/39032306213-1-release-4v100-avx512-2026-10-17-67b5da876dd6-dependencies/work.ext3"
export TMPDIR="$probe/runtime" APPTAINER_TMPDIR="$probe/runtime" APPTAINER_CACHEDIR="$probe/cache"
mkdir -p "$probe/runtime" "$probe/cache"
cd "$probe"
sha256sum --check input.sha256
date --iso-8601=seconds
hostname
python3 -c 'import os; print("CPU affinity:", sorted(os.sched_getaffinity(0))); assert len(os.sched_getaffinity(0)) >= 8'
args=(exec --fakeroot --cleanenv --containall --no-home --no-mount bind-paths,home,cwd,tmp,hostfs
  --pwd / --net --network none --nv)
for path in /usr /sbin /lib /lib64 /opt/devtools /opt/modules /etc/profile.d/lmod.sh /etc/lmod /etc/alternatives; do
  args+=(--bind "$path:$path:ro")
done
args+=(--bind /usr/bin:/bin:ro --bind "$probe:/probe:rw" --bind "$control:/control:ro"
  --env TMPDIR=/probe/runtime --env CUDA_CACHE_PATH=/probe/cuda-cache)
# Read the completed dependency image without changing its seal or identity.
python3 "$control/cp2k_image_driver.py" "$project" "$probe" "${args[@]}" --overlay "$sealed:ro" \
  "$project/containers/base/minimal-v1.sif" /bin/bash /probe/input/run-spla.sh
# A separate small image exercises CMake metadata operations without touching
# either the failed application image or the sealed dependency image.
/opt/devtools/apptainer/1.4.4/bin/apptainer overlay create --sparse --size 256 --fakeroot "$probe/openmp.ext3"
set +e
python3 "$control/cp2k_image_driver.py" "$project" "$probe" "${args[@]}" --overlay "$probe/openmp.ext3" \
  "$project/containers/base/minimal-v1.sif" /usr/bin/python3 /probe/input/openmp-repro.py
result=$?
set -e
/usr/sbin/e2fsck -fn "$probe/openmp.ext3" > "$probe/results/openmp-fsck.log" 2>&1
date --iso-8601=seconds
exit "$result"
