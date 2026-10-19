#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=00:10:00
#SBATCH --export=NIL
set -euo pipefail
run=${1:?}; target=${2:?}
export PATH=/usr/bin:/bin LD_LIBRARY_PATH= LD_PRELOAD=
export TMPDIR=$run/runtime APPTAINER_TMPDIR=$run/runtime APPTAINER_CACHEDIR=$run/cache
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
args=(exec --userns --cleanenv --containall --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd /workspace)
[[ "$target" == dsprhbm ]] || args+=(--nv)
for path in /usr /lib /lib64 /etc/alternatives /opt/devtools /opt/modules /etc/profile.d/lmod.sh; do
  args+=(--bind "$path:$path:ro")
done
[[ ! -d /etc/lmod ]] || args+=(--bind /etc/lmod:/etc/lmod:ro)
args+=(--bind "$run/control:/control:ro" --bind "$run/work:/workspace:rw"
  --bind "$run/runtime:/tmp:rw" --bind "$run/runtime:/var/tmp:rw"
  --env TMPDIR=/workspace/tmp)
[[ -z ${CUDA_VISIBLE_DEVICES:-} ]] || args+=(--env "CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES")
printf 'PREFLIGHT_JOB %s %s %s\n' "$SLURM_JOB_ID" "$SLURM_JOB_PARTITION" "$(hostname)"
/opt/devtools/apptainer/1.4.4/bin/apptainer "${args[@]}" \
  /home/stardust/sai-hpc-software/containers/base/minimal-v1.sif /bin/bash /control/inside.sh "$target"
