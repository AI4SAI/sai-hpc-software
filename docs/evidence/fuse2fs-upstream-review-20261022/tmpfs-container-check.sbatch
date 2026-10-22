#!/bin/bash
#SBATCH --job-name=fuse2fs-local-fs-probe
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=1G
#SBATCH --time=00:02:00
set -euo pipefail
root=/home/stardust/sai-hpc-software/diagnostics/fuse2fs-upstream-review-20261022
export TMPDIR="$root/capability-runtime" APPTAINER_TMPDIR="$root/capability-runtime" APPTAINER_CACHEDIR="$root/capability-cache"
/opt/devtools/apptainer/1.4.4/bin/apptainer exec --fakeroot --cleanenv --containall --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd / \
 --bind /usr:/usr:ro --bind /sbin:/sbin:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro \
 --bind "$root:/work:rw" --bind "$root/capability-runtime:/tmp:rw" \
 /home/stardust/sai-hpc-software/containers/base/minimal-v1.sif /bin/bash -c '
 set -e
 mount -t tmpfs -o size=64m tmpfs /work/local-fs
 stat -f -c %T /work/local-fs
 umount /work/local-fs
 '
