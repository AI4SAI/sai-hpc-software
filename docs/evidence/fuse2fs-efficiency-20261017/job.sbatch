#!/bin/bash
#SBATCH --job-name=sai-fuse2fs-ofd-check
#SBATCH --partition=DSPRHBM
#SBATCH --qos=rush-cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=6G
#SBATCH --time=20
#SBATCH --export=NIL
#SBATCH --output=/home/stardust/sai-hpc-software/diagnostics/cp2k-release-20261016/fuse2fs-efficiency-ofd-20261017-a/slurm-%j.log
set -euo pipefail
export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""
umask 077
bench=/home/stardust/sai-hpc-software/diagnostics/cp2k-release-20261016/fuse2fs-efficiency-ofd-20261017-a
apptainer=/opt/devtools/apptainer/1.4.4/bin/apptainer
export TMPDIR="$bench/runtime" APPTAINER_TMPDIR="$bench/runtime" APPTAINER_CACHEDIR="$bench/cache"
mkdir -p "$bench/runtime" "$bench/cache" "$bench/driver"
date --iso-8601=seconds
hostname
test ! -e "$bench/build.ext3"
cp --sparse=always --reflink=auto /home/stardust/sai-hpc-software/diagnostics/cp2k-release-20261016/fuse2fs-efficiency-20261017-a/build.ext3 "$bench/build.ext3"
"$apptainer" exec --fakeroot --cleanenv --containall --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd / --net --network none \
    --bind /usr:/usr:ro --bind /sbin:/sbin:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro --bind /opt/devtools:/opt/devtools:ro \
    --bind "$bench/input:/input:ro" --bind "$bench/driver:/results:rw" --overlay "$bench/build.ext3" --env TMPDIR=/workspace/tmp \
    /home/stardust/sai-hpc-software/containers/base/minimal-v1.sif /bin/bash /input/rebuild-candidate.sh > "$bench/driver-build.log" 2>&1
cd "$bench"
sha256sum driver/fuse2fs-45651efd-ofd > driver.sha256
/usr/bin/python3 "$bench/input/run-fuse2fs-benchmark.py" "$bench"
/usr/bin/python3 "$bench/input/summarize-fuse2fs-benchmark.py" "$bench" > "$bench/summary.log"
date --iso-8601=seconds
