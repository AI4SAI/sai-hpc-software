#!/usr/bin/env bash
# Only task-owned home directories are writable. /opt dependencies are read-only.
set -euo pipefail
mode=${1:?build or verify}; run=${2:?absolute run directory}; isa=${3:?}; shift 3
[[ "$run" == /home/stardust/sai-hpc-software/runs/elpa-* && "$run" != *:* && "$run" != *,* && "$run" != *..* ]]
case "$isa" in avx2|avx512) ;; *) exit 2 ;; esac
prefix=/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-$isa/nvidia
export TMPDIR=$run/runtime APPTAINER_TMPDIR=$run/runtime APPTAINER_CACHEDIR=$run/cache
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
args=(exec --userns --cleanenv --containall --no-home
  --no-mount bind-paths,home,cwd,tmp,hostfs --pwd / --nv)
for p in /usr /lib /lib64 /etc/alternatives /opt/devtools/nvidia /opt/devtools/openmpi /opt/devtools/saiblas /opt/modules; do
  args+=(--bind "$p:$p:ro")
done
args+=(--bind /etc/profile.d/lmod.sh:/etc/profile.d/lmod.sh:ro)
[[ ! -d /etc/lmod ]] || args+=(--bind /etc/lmod:/etc/lmod:ro)
args+=(--bind "$run/control:/control:ro" --bind "$run/work:/workspace:rw"
  --bind "$run/runtime:/tmp:rw" --bind "$run/runtime:/var/tmp:rw"
  --env TMPDIR=/tmp --env "BUILD_JOBS=${BUILD_JOBS:-8}")
if [[ -n ${CUDA_VISIBLE_DEVICES:-} ]]; then
  args+=(--env "CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES")
fi
case "$mode" in
  build)
    args+=(--bind "$run/install:$prefix:rw" --bind "$run/input:/input:ro"
      --bind /opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/comm_libs/12.9/hpcx/hpcx-2.25.1/hcoll:/opt/mellanox/hcoll:ro)
    image=/home/stardust/sai-hpc-software/containers/base/minimal-v1.sif ;;
  verify) image=$run/elpa-$isa.sif ;;
  *) exit 2 ;;
esac
exec /opt/devtools/apptainer/1.4.4/bin/apptainer "${args[@]}" "$image" "$@"
