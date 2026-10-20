#!/usr/bin/env bash
#SBATCH --job-name=cp2k-tblite-dependencies
#SBATCH --partition=4V100
#SBATCH --qos=flood-1o2gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --time=120
#SBATCH --export=NIL
set -eo pipefail
export HOME=/home/stardust PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""
source /etc/profile.d/lmod.sh
module load apptainer/1.4.4
set -u
root=$HOME/sai-hpc-software
run=$root/runs/cp2k-tblite-$SLURM_JOB_ID
mkdir -p "$run/runtime" "$run/cache"
export TMPDIR=$run/runtime APPTAINER_TMPDIR=$run/runtime APPTAINER_CACHEDIR=$run/cache
apptainer overlay create --fakeroot --sparse --size 8192 "$run/work.ext3"
apptainer exec --fakeroot --cleanenv --containall --no-home \
  --no-mount bind-paths,home,cwd,tmp,hostfs --pwd / --net --network none \
  --bind /usr:/usr:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro \
  --bind /opt/devtools:/opt/devtools:ro --bind /opt/modules:/opt/modules:ro \
  --bind /etc/profile.d/lmod.sh:/etc/profile.d/lmod.sh:ro \
  --bind "$root/cache/cp2k-probe:/input:ro" \
  --bind "$root/controller/cp2k-probe-20260909:/recipe:ro" \
  --bind "$run:/output" \
  --overlay "$run/work.ext3" "$root/containers/base/minimal-v1.sif" \
  /usr/bin/bash -ec '
    export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""
    source /etc/profile.d/lmod.sh
    module use /opt/modules/modulefiles/devtools
    module load cmake/3.31.6 saiblas/2603-gnu-auto
    mkdir -p /workspace/tmp
    export TMPDIR=/workspace/tmp
    bash /recipe/cp2k_dependencies.sh /input /workspace/dependencies /opt/software/cp2k-dependencies/tblite
    rm -rf /output/tblite-stage
    cp -a /opt/software/cp2k-dependencies/tblite /output/tblite-stage
    tar -czf /output/tblite-dependencies.tar.gz -C /output --transform='s/^tblite-stage$/tblite/' tblite-stage
    echo CP2K_TBLITE_DEPENDENCIES_COMPLETE
  '
mkdir -p "$root/cache/cp2k-dependencies"
install -m 0644 "$run/tblite-dependencies.tar.gz" "$root/cache/cp2k-dependencies/tblite-dependencies.tar.gz"
sha256sum "$root/cache/cp2k-dependencies/tblite-dependencies.tar.gz" > "$root/cache/cp2k-dependencies/tblite-dependencies.tar.gz.sha256"
