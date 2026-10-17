#!/bin/bash
set -euo pipefail
export TMPDIR=/workspace/tmp
export PATH=/opt/devtools/gcc/14.3.0/bin:/usr/bin:/bin
export LD_LIBRARY_PATH=/opt/devtools/gcc/14.3.0/lib64
python3 /input/patch-fuse2fs-inherited-ofd.py /workspace/fuse-driver/src/misc/fuse2fs.c ab3794d86b12a8227b4a87823349e974df06f39c42971dcf2983ddf57cf82696
cd /workspace/fuse-driver/build
make -j8 -C misc fuse2fs
install -m755 misc/fuse2fs /results/fuse2fs-45651efd-ofd
sha256sum /results/fuse2fs-45651efd-ofd
