#!/bin/bash
#SBATCH --job-name=fuse2fs-upstream-review
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=8G
#SBATCH --time=00:25:00
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LD_LIBRARY_PATH="" PYTHONDONTWRITEBYTECODE=1
export FUSE_REVIEW_ROOT=/home/stardust/sai-hpc-software/diagnostics/fuse2fs-upstream-review-20261022
root="$FUSE_REVIEW_ROOT"
export TMPDIR="$root/runtime-c" APPTAINER_TMPDIR="$root/runtime-c" APPTAINER_CACHEDIR="$root/cache-c"
mkdir -p "$TMPDIR" "$APPTAINER_CACHEDIR" "$root/workspace-c" "$root/drivers-c" "$root/workspace-c/tmp"
/opt/devtools/apptainer/1.4.4/bin/apptainer exec --cleanenv --containall --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd / \
 --bind /usr:/usr:ro --bind /sbin:/sbin:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro --bind /opt/devtools:/opt/devtools:ro \
 --bind "$root/input-c:/input:ro" --bind "$root:/archive:ro" --bind "$root/workspace-c:/workspace:rw" --bind "$root/workspace-c/tmp:/tmp:rw" \
 --bind "$root/drivers-c:/results:rw" \
 --bind /home/stardust/sai-hpc-software/diagnostics/cp2k-release-20261016/fuse2fs-efficiency-20261017-a/input:/dependency:ro \
 /home/stardust/sai-hpc-software/containers/base/minimal-v1.sif /bin/bash /input/build.sh > "$root/build-c.log" 2>&1
/usr/bin/python3 -B "$root/input-c/run-tests.py"
