#!/bin/bash
#SBATCH --job-name=sai-fuse2fs-ro-check
#SBATCH --partition=DSPRHBM
#SBATCH --qos=rush-cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=28G
#SBATCH --time=20
#SBATCH --export=NIL
#SBATCH --output=/home/stardust/sai-hpc-software/diagnostics/cp2k-release-20261016/fuse2fs-readonly-20261017-a/slurm-%j.log
set -euo pipefail
export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD="" PYTHONDONTWRITEBYTECODE=1
umask 077
bench=/home/stardust/sai-hpc-software/diagnostics/cp2k-release-20261016/fuse2fs-readonly-20261017-a
old=/home/stardust/sai-hpc-software/diagnostics/cp2k-release-20261016/fuse2fs-efficiency-20261017-a
private=/home/stardust/sai-hpc-software/cache/tools/fuse2fs-0680f553de17cba684a6f5ea265c9d80bd6d576d68aebc4e788e2609a66ad5e9/bin
apptainer=/opt/devtools/apptainer/1.4.4/bin/apptainer
export TMPDIR="$bench/runtime" APPTAINER_TMPDIR="$bench/runtime" APPTAINER_CACHEDIR="$bench/cache"
cd "$bench"
sha256sum --check input.sha256
date --iso-8601=seconds
hostname
test ! -e "$bench/build.ext3"
cp --sparse=always --reflink=auto "$old/build.ext3" "$bench/build.ext3"
python3 - "$bench" "$private" <<'PY'
from pathlib import Path
import sys
base, driver = map(Path, sys.argv[1:])
config = Path('/opt/devtools/apptainer/1.4.4/etc/apptainer/apptainer.conf').read_text()
(base / 'build-apptainer.conf').write_text(config + '\nbinary path = ' + str(driver) + ':$PATH:/usr/bin:/bin:/usr/sbin:/sbin\n')
PY
printf '%s  %s\n' 0680f553de17cba684a6f5ea265c9d80bd6d576d68aebc4e788e2609a66ad5e9 "$private/fuse2fs" | sha256sum --check
"$apptainer" --config "$bench/build-apptainer.conf" exec --fakeroot --cleanenv --containall --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd / --net --network none \
    --bind /usr:/usr:ro --bind /sbin:/sbin:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro --bind /opt/devtools:/opt/devtools:ro \
    --bind "$bench/input:/input:ro" --bind "$old/input:/archive:ro" --bind "$bench/driver:/results:rw" --overlay "$bench/build.ext3" --env TMPDIR=/workspace/tmp \
    /home/stardust/sai-hpc-software/containers/base/minimal-v1.sif /bin/bash /input/rebuild.sh > "$bench/driver-build.log" 2>&1
sha256sum driver/fuse2fs driver/fuse2fs-readonly.patch > driver.sha256
python3 -B "$bench/input/probe.py" "$bench"
date --iso-8601=seconds
