#!/usr/bin/env bash
# Only trusted, tiny namespace/persistence diagnostics; no external source runs.
set -eo pipefail
source /etc/profile.d/lmod.sh
module load apptainer/1.4.4
set -u
root="$HOME/sai-hpc-software"
probe=$(mktemp -d "$root/runs/overlay-probe.XXXXXXXX")
mkdir "$probe/runtime" "$probe/cache"
export TMPDIR="$probe/runtime" APPTAINER_TMPDIR="$probe/runtime" APPTAINER_CACHEDIR="$probe/cache"
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
printf 'PROBE=%s\n' "$probe"
apptainer overlay create --fakeroot --sparse --size 256 "$probe/overlay.img"
flags=(--fakeroot --cleanenv --containall --no-home --no-mount bind-paths,home,cwd,tmp --pwd / --bind /usr:/usr:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro --bind /opt/devtools:/opt/devtools:ro)
base="$root/containers/base/minimal-v1.sif"
apptainer exec "${flags[@]}" --overlay "$probe/overlay.img" "$base" /bin/sh -ec 'mkdir -p /opt/software/probe /workspace; printf "persisted\n" > /opt/software/probe/result; test ! -e /home/stardust/.ssh; test ! -w /opt/devtools; cat /opt/software/probe/result'
apptainer exec "${flags[@]}" --overlay "$probe/overlay.img:ro" "$base" /bin/sh -ec 'cat /opt/software/probe/result; test ! -e /home/stardust/sai-hpc-software/controller'
cp "$base" "$probe/probe.sif"
apptainer sif add --datatype 4 --partfs 2 --parttype 4 --partarch 2 --groupid 1 "$probe/probe.sif" "$probe/overlay.img"
apptainer exec "${flags[@]}" "$probe/probe.sif" /bin/sh -ec 'cat /opt/software/probe/result'
printf 'PERSISTENCE_OK=%s\n' "$probe/probe.sif"
apptainer exec "${flags[@]}" --overlay "$probe/overlay.img" "$base" /bin/bash -ec 'mkdir -p /workspace/export/{bin,opt/software,opt/devtools,dev,proc,sys,etc,usr,lib,lib64,tmp,home,root}; cp -a /opt/software/probe /workspace/export/opt/software/; cp /bin/bash /workspace/export/bin/bash; ln -s bash /workspace/export/bin/sh; mksquashfs /workspace/export /workspace/final.squashfs -noappend -processors 1 -all-root -no-xattrs >/dev/null'
apptainer exec "${flags[@]}" --overlay "$probe/overlay.img:ro" "$base" /usr/bin/cat /workspace/final.squashfs > "$probe/final.squashfs"
apptainer sif new "$probe/final.sif"
apptainer sif add --datatype 4 --partfs 1 --parttype 2 --partarch 2 --groupid 1 "$probe/final.sif" "$probe/final.squashfs"
apptainer exec "${flags[@]}" "$probe/final.sif" /bin/sh -ec 'cat /opt/software/probe/result; if touch /opt/software/probe/not-allowed 2>/dev/null; then exit 1; fi'
printf 'SQUASHFS_OK=%s\n' "$probe/final.sif"
