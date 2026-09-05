#!/usr/bin/env bash
set -euo pipefail
root=${1:?rootfs destination required}
if [[ -f "$root/.sai-hpc-ci-rootfs-v1" ]]; then exit 0; fi
rm -rf "$root"
mkdir -p "$root"/{bin,etc,dev,proc,sys,tmp,var/tmp,home,root,lib64,usr/bin}
cp -L /bin/bash "$root/bin/bash"; ln -s bash "$root/bin/sh"
while read -r library; do mkdir -p "$root$(dirname "$library")"; cp -L "$library" "$root$library"; done < <(ldd /bin/bash | sed -n 's/.*=> \([^ ]*\).*/\1/p')
cp -L /lib64/ld-linux-x86-64.so.2 "$root/lib64/ld-linux-x86-64.so.2"
cp -L /etc/hosts /etc/resolv.conf "$root/etc/"
printf 'root:x:0:0:root:/root:/bin/sh\n' > "$root/etc/passwd"
printf 'root:x:0:\n' > "$root/etc/group"
chmod 1777 "$root/tmp" "$root/var/tmp"; chmod -R a+rX "$root"
touch "$root/.sai-hpc-ci-rootfs-v1"
