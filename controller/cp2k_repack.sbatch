#!/usr/bin/env bash
# Repack a completed CP2K build left in a file-backed overlay.
# This is intentionally separate from the source build job so a final runtime
# failure cannot discard an otherwise successful compiler run.
#SBATCH --job-name=cp2k-repack
#SBATCH --partition=4V100
#SBATCH --qos=flood-1o2gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --time=20
#SBATCH --export=NIL
set -euo pipefail
export HOME=/home/stardust USER=stardust LOGNAME=stardust PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""
source /etc/profile.d/lmod.sh
module load apptainer/1.4.4
root=/home/stardust/sai-hpc-software
overlay=$root/runs/cp2k-dependency-1216695/work.ext3
run=$root/runs/cp2k-repack-1216695
base=$root/containers/base/minimal-v1.sif
mkdir -p "$run/runtime" "$run/cache"
export TMPDIR=$run/runtime APPTAINER_TMPDIR=$run/runtime APPTAINER_CACHEDIR=$run/cache
test -s "$overlay"
apptainer exec --fakeroot --cleanenv --containall --no-home \
  --no-mount bind-paths,home,cwd,tmp,hostfs --pwd / --net --network none --nv \
  --bind /usr:/usr:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro \
  --bind /opt/devtools:/opt/devtools:ro --bind /opt/modules:/opt/modules:ro \
  --bind /etc/profile.d/lmod.sh:/etc/profile.d/lmod.sh:ro \
  --bind "$root/controller:/control:ro" --bind "$run:/output" \
  --overlay "$overlay" "$base" /usr/bin/bash -ec '
    export USER=stardust LOGNAME=stardust
    source /etc/profile.d/lmod.sh
    module use /opt/modules/modulefiles/devtools
    module load openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto cuda/12.9.1 saiblas/2603-gnu-auto nvmplibs/26.7-tmp
    export LD_LIBRARY_PATH=/opt/software/cp2k/latest-v100/lib64:/opt/software/cp2k/latest-v100/lib:${LD_LIBRARY_PATH:-}
    /opt/software/cp2k/latest-v100/bin/cp2k.psmp --version
    ldd /opt/software/cp2k/latest-v100/bin/cp2k.psmp | tee /output/ldd.txt
    ! grep -q "not found" /output/ldd.txt
    rm -rf /workspace/export /workspace/final.squashfs
    bash /control/create_rootfs.sh /workspace/export
    cp -a /opt/software/cp2k /workspace/export/opt/software/
    mksquashfs /workspace/export /workspace/final.squashfs -noappend -all-root -no-xattrs -processors 8 >/dev/null
    cp /workspace/final.squashfs /output/cp2k-latest-v100.squashfs
  '
apptainer sif new "$run/cp2k-latest-v100.sif"
apptainer sif add --datatype 4 --partfs 1 --parttype 2 --partarch 2 --groupid 1 \
  "$run/cp2k-latest-v100.sif" "$run/cp2k-latest-v100.squashfs"
chmod 0444 "$run/cp2k-latest-v100.sif"
sha256sum "$run/cp2k-latest-v100.sif" > "$run/cp2k-latest-v100.sif.sha256"
echo CP2K_REPACK_COMPLETE
