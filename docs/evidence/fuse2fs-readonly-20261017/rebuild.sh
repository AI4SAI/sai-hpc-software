#!/bin/bash
set -euo pipefail
export TMPDIR=/workspace/tmp
export PATH=/opt/devtools/gcc/14.3.0/bin:/usr/bin:/bin
export LD_LIBRARY_PATH=/opt/devtools/gcc/14.3.0/lib64
archive=/archive/e2fsprogs-45651efd.tar.gz
printf '%s  %s\n' 3ba72ea2ec624fcbe8c155656623c16da92e07dff4a7ffb981dcadbd9990ccf4 "$archive" | sha256sum --check
tar -xOf "$archive" tytso-e2fsprogs-45651ef/misc/fuse2fs.c > /workspace/fuse-driver/src/misc/fuse2fs.c
python3 /input/patch-fuse2fs-readonly.py /workspace/fuse-driver/src/misc/fuse2fs.c /results/fuse2fs-readonly.patch
printf '%s  %s\n' c63d1fb25510d316836952953e9adddacd3214cd91d6002bf3b16aed350c4ddc /workspace/fuse-driver/src/misc/fuse2fs.c | sha256sum --check
cmp /input/fuse2fs-readonly.patch /results/fuse2fs-readonly.patch
cd /workspace/fuse-driver/build
make -j8 -C misc fuse2fs
install -m555 misc/fuse2fs /results/fuse2fs
sha256sum /workspace/fuse-driver/src/misc/fuse2fs.c /results/fuse2fs
./misc/fuse2fs --version
ldd ./misc/fuse2fs
