#!/bin/bash
#SBATCH --job-name=sai-cp2k-config-probe
#SBATCH --partition=4V100
#SBATCH --qos=flood-1o2gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --time=20
#SBATCH --export=NIL
#SBATCH --output=/home/stardust/sai-hpc-software/runs/cp2k-spack-config-probe-20261017-d/results/slurm-%j.log
set -euo pipefail
export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""
umask 077
project=/home/stardust/sai-hpc-software
probe="$project/runs/cp2k-spack-config-probe-20261017-d"
control="$project/controller/1e7d736e5884fe207460a3e6baf0aeddb5bdb4aa/39032306213-1-release-4v100-avx512-2026-10-17-67b5da876dd6"
dependency="$project/runs/39032306213-1-release-4v100-avx512-2026-10-17-67b5da876dd6-dependencies"
export TMPDIR="$probe/runtime" APPTAINER_TMPDIR="$probe/runtime" APPTAINER_CACHEDIR="$probe/cache"
export PYTHONPATH="$control"
mkdir -p "$probe/cache"
cd "$probe"
sha256sum --check input.sha256
sha256sum "$dependency/work.ext3" > "$probe/results/image-before.sha256"
cmp "$probe/results/image-before.sha256" "$project/runs/cp2k-spack-config-probe-20261017-c/results/image-before.sha256"
echo PRIOR_C_IMAGE_UNCHANGED > "$probe/results/prior-c-integrity.log"
date --iso-8601=seconds
hostname
args=(exec --fakeroot --cleanenv --containall --no-home --no-mount bind-paths,home,cwd,tmp,hostfs
  --pwd / --net --network none --nv)
for path in /usr /sbin /lib /lib64 /opt/devtools /opt/modules /etc/profile.d/lmod.sh /etc/lmod /etc/alternatives; do
  args+=(--bind "$path:$path:ro")
done
args+=(--bind /usr/bin:/bin:ro --bind "$probe:/probe:rw" --bind "$control:/control:ro"
  --bind "$dependency/results:/dep-results:ro" --env TMPDIR=/probe/runtime --env CUDA_CACHE_PATH=/probe/cuda-cache)
# This image's historical seal is invalidated by probe B. It is only a
# diagnostic library source here, never a release checkpoint or new identity.
set +e
python3 "$probe/input/cp2k_image_driver.py" "$project" "$probe" "${args[@]}" --overlay "$dependency/work.ext3:ro" \
  "$project/containers/base/minimal-v1.sif" /bin/bash /probe/input/config-probe.sh
config_status=$?
set -e
printf '%s\n' "$config_status" > "$probe/results/config.exit"
sha256sum "$dependency/work.ext3" > "$probe/results/image-after.sha256"
cmp "$probe/results/image-before.sha256" "$probe/results/image-after.sha256"
echo READONLY_IMAGE_UNCHANGED
date --iso-8601=seconds
exit "$config_status"
