#!/bin/bash
set -euo pipefail
export PATH=/opt/devtools/gcc/14.3.0/bin:/usr/bin:/bin
export LD_LIBRARY_PATH=/opt/devtools/gcc/14.3.0/lib64
export TMPDIR=/workspace/tmp
mkdir -p /workspace/tmp /workspace/source /workspace/build /workspace/dev
printf '%s  %s\n' cfa8460d23b5143d14bb336b3008784df72a5e9fc32057ec1dc85beadca9aecb /dependency/libfuse3-dev_3.14.0-5build1_amd64.deb | sha256sum --check
tar -xzf /archive/e2fsprogs-master-8fd7952.tar.gz -C /workspace/source
dpkg-deb -x /dependency/libfuse3-dev_3.14.0-5build1_amd64.deb /workspace/dev
cd /workspace/build
export CC=gcc CXX=g++
export fuse3_CFLAGS=-I/workspace/dev/usr/include/fuse3
export fuse3_LIBS=/usr/lib/x86_64-linux-gnu/libfuse3.so.3
../source/configure --prefix=/workspace/uninstalled --disable-nls --disable-fsck --disable-e2initrd-helper --disable-uuidd --enable-fuse2fs --disable-fuse4fs
make -j8 libs
for variant in master norecovery inherited-lock combined; do
    if [ "$variant" != master ]; then
        cp "/input/$variant.c" /workspace/source/misc/fuse2fs.c
    fi
    mkdir -p "/results/$variant"
    cp /workspace/source/misc/fuse2fs.c "/results/$variant/fuse2fs.c"
    # Apply the existing upstream missing-header workaround only to this translation unit.
    make -C misc fuse2fs.o V=1 CFLAGS="-O2 -g -include stdbool.h" > "/results/$variant/build.log" 2>&1
    make -j8 -C misc fuse2fs >> "/results/$variant/build.log" 2>&1
    install -m555 misc/fuse2fs "/results/$variant/fuse2fs"
    sha256sum "/results/$variant/fuse2fs" "/results/$variant/fuse2fs.c" > "/results/$variant/SHA256"
    ldd "/results/$variant/fuse2fs" > "/results/$variant/ldd.log"
done
