#!/bin/bash
# User launcher for the reviewed image; no unpacked runtime or host MPI is needed.
set -euo pipefail
package=$(cd -- "$(dirname -- "$0")" && pwd -P)
image="$package/neptrainkit-3.1.5.sif"
[[ -f "$image" ]] || { echo "Missing prepared SIF: $image" >&2; exit 1; }
app=/opt/devtools/apptainer/1.4.4/bin/apptainer
args=(exec --cleanenv --no-home --no-mount bind-paths,home,hostfs --pwd "$PWD")
for path in /usr /sbin /lib /lib64 /opt/devtools /etc/alternatives /etc/fonts; do
    args+=(--bind "$path:$path:ro")
done
args+=(--bind /home:/home:rw --bind /org:/org:rw --bind "$PWD:$PWD:rw")
if [[ -n "${CUDA_VISIBLE_DEVICES:-}" && "$CUDA_VISIBLE_DEVICES" != -1 ]]; then
    args+=(--nv --env "CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES")
fi
if [[ -d /tmp/.X11-unix ]]; then args+=(--bind /tmp/.X11-unix:/tmp/.X11-unix:ro); fi
for name in DISPLAY XAUTHORITY TMPDIR XDG_RUNTIME_DIR; do
    if [[ -n "${!name:-}" ]]; then args+=(--env "$name=${!name}"); fi
done
args+=(--env PYTHONDONTWRITEBYTECODE=1 --env PYTHONNOUSERSITE=1)
args+=(--env LD_LIBRARY_PATH=/opt/devtools/gcc/14.3.0/lib64:/opt/devtools/gcc/14.3.0/lib)
exec "$app" "${args[@]}" "$image" /usr/bin/env "HOME=$HOME" \
    /opt/apps/neptrainkit/3.1.5/bin/nepkit "$@"
