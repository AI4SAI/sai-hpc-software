#!/bin/bash
set -euo pipefail
mkdir -p /workspace/tmp /workspace/fuse-driver/src /workspace/fuse-driver/build /workspace/fuse-driver/dev
export TMPDIR=/workspace/tmp
export PATH=/opt/devtools/gcc/14.3.0/bin:/usr/bin:/bin
export LD_LIBRARY_PATH=/opt/devtools/gcc/14.3.0/lib64
cd /input
printf '%s  %s\n' 3ba72ea2ec624fcbe8c155656623c16da92e07dff4a7ffb981dcadbd9990ccf4 e2fsprogs-45651efd.tar.gz cfa8460d23b5143d14bb336b3008784df72a5e9fc32057ec1dc85beadca9aecb libfuse3-dev_3.14.0-5build1_amd64.deb | sha256sum --check
tar --no-same-owner -xzf /input/e2fsprogs-45651efd.tar.gz --strip-components=1 -C /workspace/fuse-driver/src
dpkg-deb -x /input/libfuse3-dev_3.14.0-5build1_amd64.deb /workspace/fuse-driver/dev
cd /workspace/fuse-driver/build
export CC=gcc CXX=g++
export fuse3_CFLAGS=-I/workspace/fuse-driver/dev/usr/include/fuse3
export fuse3_LIBS=/usr/lib/x86_64-linux-gnu/libfuse3.so.3
../src/configure --prefix=/opt/sai/fuse2fs-45651efd --disable-nls --disable-fsck --disable-e2initrd-helper --disable-uuidd --enable-fuse2fs
make -j8 libs
make -j8 -C misc fuse2fs
./misc/fuse2fs --version
ldd ./misc/fuse2fs
install -m755 ./misc/fuse2fs /results/fuse2fs-45651efd
sha256sum /results/fuse2fs-45651efd > /results/fuse2fs-45651efd.sha256
cp config.log /results/config.log
