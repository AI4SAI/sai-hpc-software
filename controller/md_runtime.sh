#!/usr/bin/env bash
# Each host Open MPI rank enters this SIF. No source or compilation runs on host.
set -euo pipefail
: "${SAI_SOFTWARE_ROOT:?}"
: "${SAI_MD_VERSION:?}"
: "${SAI_MD_IMAGE:?explicit candidate or accepted immutable image required}"
: "${SLURM_JOB_PARTITION:?a compute allocation is required}"
case "$SLURM_JOB_PARTITION" in
  4V100) target=4v100-avx512 ;;
  16V100) target=16v100-avx2 ;;
  8V100V0) target=8v100v0-avx512 ;;
  *) echo 'unvalidated MD target' >&2; exit 2 ;;
esac
[[ "$SAI_MD_VERSION" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]
program=$1; shift
case "$program" in
  lmp) executable="/opt/software/lammps/$SAI_MD_VERSION/$target/bin/lmp" ;;
  dp|python) executable="/opt/software/deepmd-kit/$SAI_MD_VERSION/$target/bin/$program" ;;
  *) echo 'only lmp, dp and the installed DeepMD Python are exposed' >&2; exit 2 ;;
esac
root=$(realpath -e "$SAI_SOFTWARE_ROOT/experimental/deepmd-lammps")
image=$(realpath -e "$SAI_MD_IMAGE")
[[ "$image" == "$root/containers/software/deepmd-lammps/$SAI_MD_VERSION/$target/"*.sif && ! -L "$SAI_MD_IMAGE" ]]
work=$(pwd -P)
runtime=$(realpath -e "${TMPDIR:?host MPI TMPDIR must be below experimental runtime-tests}")
[[ "$runtime" == "$root/runtime-tests/"* && -d "$runtime" ]]
for path in "$work" "$runtime"; do
  case "$path" in *:*|*,*|*$'\n'*) exit 2;; esac
done
export APPTAINER_TMPDIR="$runtime" APPTAINER_CACHEDIR="$runtime/apptainer-cache"
mkdir -p "$APPTAINER_CACHEDIR"
args=(apptainer exec --nv --cleanenv --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd /work)
for path in /usr /lib /lib64 /opt/devtools /opt/apps; do args+=(--bind "$path:$path:ro"); done
args+=(--bind "$work:/work:rw" --bind "$runtime:$runtime:rw" --env "TMPDIR=$runtime")
while IFS= read -r name; do
  case "$name" in
    SLURM_*|OMPI_*|OPAL_*|PMIX_*|PMI_*|PRTE_*|UCX_*|NCCL_*|CUDA_*|FI_*|OMP_*|DP_*)
      args+=(--env "$name=${!name}") ;;
  esac
done < <(compgen -e)
exec "${args[@]}" "$image" /usr/bin/bash --noprofile --norc -c \
  'driver=${LD_LIBRARY_PATH:-}; source "$1"; shift; export LD_LIBRARY_PATH="$driver${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"; exec "$@"' bash \
  "/opt/software/lammps/$SAI_MD_VERSION/$target/share/sai/runtime-env.sh" "$executable" "$@"
