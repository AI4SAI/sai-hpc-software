#!/bin/bash
set -euo pipefail
export PATH=/opt/devtools/gcc/14.3.0/bin:/usr/bin:/bin
export LD_LIBRARY_PATH=/opt/devtools/gcc/14.3.0/lib64 TMPDIR=/workspace/tmp
cd /workspace/build
for variant in master norecovery inherited-lock combined; do
    if [ "$variant" = master ]; then
        tar -xOf /archive/e2fsprogs-master-8fd7952.tar.gz misc/fuse2fs.c > /workspace/source/misc/fuse2fs.c
    else
        cp "/input/$variant.c" /workspace/source/misc/fuse2fs.c
    fi
    mkdir -p "/results/$variant"
    cp /workspace/source/misc/fuse2fs.c "/results/$variant/fuse2fs.c"
    # Retain old build outputs; missing targets force recompilation despite filesystem timestamp skew.
    if [ -f misc/fuse2fs.o ]; then mv misc/fuse2fs.o "/results/$variant/previous-fuse2fs.o"; fi
    if [ -f misc/fuse2fs ]; then mv misc/fuse2fs "/results/$variant/previous-fuse2fs"; fi
    make -j8 -C misc fuse2fs V=1 LOCAL_CFLAGS="-include stdbool.h" > "/results/$variant/build.log" 2>&1
    install -m555 misc/fuse2fs "/results/$variant/fuse2fs"
    sha256sum "/results/$variant/fuse2fs" "/results/$variant/fuse2fs.c" > "/results/$variant/SHA256"
    ldd "/results/$variant/fuse2fs" > "/results/$variant/ldd.log"
done
cp config.log /results/config.log
python3 - <<'CHECK'
from pathlib import Path
import hashlib
hashes=[hashlib.sha256((Path('/results')/v/'fuse2fs').read_bytes()).hexdigest() for v in ['master','norecovery','inherited-lock','combined']]
assert len(set(hashes))==4, hashes
print('All four variants have distinct binaries:',hashes)
CHECK
