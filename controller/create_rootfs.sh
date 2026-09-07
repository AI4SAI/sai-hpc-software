#!/usr/bin/env bash
# Trusted helper used INSIDE the file-backed container during packaging.
set -euo pipefail
root=$1
[[ "$root" == /workspace/export ]] || { echo 'packaging must stay in /workspace' >&2; exit 2; }
[[ ! -e "$root" ]] || { echo 'export tree already exists' >&2; exit 2; }
mkdir -p "$root"/{bin,etc/profile.d,dev,proc,sys,tmp,var/tmp,home,root,lib64,usr,lib,opt/devtools,opt/modules,workspace,input,control}
cp -L /bin/bash "$root/bin/bash"
ln -s bash "$root/bin/sh"
while read -r library; do
    mkdir -p "$root$(dirname "$library")"
    cp -L "$library" "$root$library"
done < <(ldd /bin/bash | sed -n 's/.*=> \([^ ]*\).*/\1/p')
cp -L /lib64/ld-linux-x86-64.so.2 "$root/lib64/ld-linux-x86-64.so.2"
cp -L /etc/hosts /etc/resolv.conf "$root/etc/"
touch "$root/etc/profile.d/lmod.sh"
printf 'root:x:0:0:root:/root:/bin/bash\n' > "$root/etc/passwd"
printf 'root:x:0:\n' > "$root/etc/group"
chmod 1777 "$root/tmp" "$root/var/tmp"
