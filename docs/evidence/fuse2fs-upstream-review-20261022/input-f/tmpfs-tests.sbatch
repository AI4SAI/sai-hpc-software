#!/bin/bash
#SBATCH --job-name=fuse2fs-local-fs-tests
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=3G
#SBATCH --time=00:12:00
set -euo pipefail
root=/home/stardust/sai-hpc-software/diagnostics/fuse2fs-upstream-review-20261022
export TMPDIR="$root/runtime-f" APPTAINER_TMPDIR="$root/runtime-f" APPTAINER_CACHEDIR="$root/cache-f"
mkdir -p "$TMPDIR" "$APPTAINER_CACHEDIR" "$root/local-fs-f" "$root/tmpfs-results-f"
/opt/devtools/apptainer/1.4.4/bin/apptainer exec --fakeroot --cleanenv --containall --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd / \
 --bind /usr:/usr:ro --bind /sbin:/sbin:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro \
 --bind /dev/fuse:/dev/fuse --bind "$root:/work:rw" --bind "$TMPDIR:/tmp:rw" \
 /home/stardust/sai-hpc-software/containers/base/minimal-v1.sif /bin/bash -c '
 set -euo pipefail
 mount -t tmpfs -o size=1g tmpfs /work/local-fs-f
 archive_results() {
     status=$?
     trap - EXIT
     tar --sparse -czf /work/tmpfs-results-f/images-and-logs.tar.gz -C /work/local-fs-f . || status=1
     umount /work/local-fs-f || status=1
     exit "$status"
 }
 trap archive_results EXIT
 stat -f -c %T /work/local-fs-f > /work/tmpfs-results-f/filesystem.txt
 id > /work/tmpfs-results-f/identity.txt
 uname -a > /work/tmpfs-results-f/kernel.txt
 python3 -B /work/input-f/run-tmpfs-tests.py
 '
