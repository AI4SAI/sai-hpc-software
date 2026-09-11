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
implementation=${SAI_MD_IMPLEMENTATION:-candidate}
[[ "$implementation" == baseline || "$implementation" == candidate ]]
case "$program" in
  lmp) executable="/opt/software/lammps/$SAI_MD_VERSION/$target/bin/lmp" ;;
  dp|python) executable="/opt/software/deepmd-kit/$SAI_MD_VERSION/$target/bin/$program" ;;
  *) echo 'only lmp, dp and the installed DeepMD Python are exposed' >&2; exit 2 ;;
esac
if [[ "$implementation" == baseline ]]; then
  case "$program" in
    lmp) executable=/opt/apps/lammps/lammps-4Jul2026-deepmd3.2.0-plumed2.10.1-nvhpc263-ompi5010-sm70/bin/lmp ;;
    dp|python) executable="/opt/apps/conda_env/deepmd-kit-3.2.0/bin/$program" ;;
  esac
fi
program_argv=("$executable")
if [[ -n "${SAI_MD_PROFILE_LMP:-}" ]]; then
  [[ "$program" == lmp && -n "${SAI_MD_TEST_CONTROL:-}" ]]
  profiler=$(realpath -e "$SAI_MD_PROFILE_LMP")
  [[ "$profiler" == /opt/devtools/* && -x "$profiler" ]]
  # Profile inside the SIF so cleanenv does not discard Nsight's CUDA tracing
  # injection between the profiler and the actual LAMMPS child process.
  program_argv=("$profiler" profile --trace=cuda --sample=none --cpuctxsw=none \
    --force-overwrite=false --output /work/cuda-trace "$executable")
fi
if [[ -n "${SAI_MD_PERFORMANCE_CPU:-}" ]]; then
  [[ "$program" == lmp && -n "${SAI_MD_TEST_CONTROL:-}" && "$SAI_MD_PERFORMANCE_CPU" =~ ^[0-9]+$ ]]
  program_argv=(/usr/bin/python3 /control/md_performance_exec.py "${program_argv[@]}")
fi
root=$(realpath -e "$SAI_SOFTWARE_ROOT/experimental/deepmd-lammps")
image=$(realpath -e "$SAI_MD_IMAGE")
[[ "$image" == "$root/containers/software/deepmd-lammps/$SAI_MD_VERSION/$target/"*.sif && ! -L "$SAI_MD_IMAGE" ]]
work=$(pwd -P)
runtime=$(realpath -e "${TMPDIR:?host MPI TMPDIR must be below experimental runtime-tests}")
[[ "$runtime" == "$root/runtime-tests/"* && -d "$runtime" ]]
if [[ "${SAI_MD_TRACE_RANKS:-}" == 1 ]]; then
  [[ "$work" == "$root/runtime-tests/"* && ! -L "$work/sai-ranks" ]]
  rank=${OMPI_COMM_WORLD_RANK:-0}
  size=${OMPI_COMM_WORLD_SIZE:-1}
  [[ "$rank" =~ ^[0-9]+$ && "$size" =~ ^[0-9]+$ ]]
  mkdir -p "$work/sai-ranks"
  [[ ! -L "$work/sai-ranks/rank-$rank.tsv" ]]
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(hostname)" "$rank" "$size" "$image" \
    "$implementation" "$target" "$executable" "${MPI_HOME:-}" > "$work/sai-ranks/rank-$rank.tsv"
fi
for path in "$work" "$runtime"; do
  case "$path" in *:*|*,*|*$'\n'*) exit 2;; esac
done
export APPTAINER_TMPDIR="$runtime" APPTAINER_CACHEDIR="$runtime/apptainer-cache"
mkdir -p "$APPTAINER_CACHEDIR"
args=(apptainer exec --nv --cleanenv --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd /work)
for path in /usr /lib /lib64 /opt/devtools /opt/apps; do args+=(--bind "$path:$path:ro"); done
args+=(--bind "$work:/work:rw" --bind "$runtime:$runtime:rw" --env "TMPDIR=$runtime")
args+=(--env "SAI_MD_ALLOCATED_JOB=$SLURM_JOB_ID" --env "SAI_MD_ALLOCATED_NODE=$(hostname)")
if [[ -n "${SAI_MD_PERFORMANCE_CPU:-}" ]]; then args+=(--env "SAI_MD_PERFORMANCE_CPU=$SAI_MD_PERFORMANCE_CPU"); fi
if [[ -n "${SAI_MD_TEST_CONTROL:-}" ]]; then
  control=$(realpath -e "$SAI_MD_TEST_CONTROL")
  [[ "$control" == "$root/controller/"* && -d "$control" && ! -L "$SAI_MD_TEST_CONTROL" ]]
  args+=(--bind "$control:/control:ro")
fi
if [[ "$implementation" == baseline ]]; then
  : "${SAI_MD_TEST_CONTROL:?baseline comparison requires trusted test harness}"
  args+=(--bind /opt/modules:/opt/modules:ro --bind /etc/profile.d/lmod.sh:/etc/profile.d/lmod.sh:ro)
  if [[ -d /etc/lmod ]]; then args+=(--bind /etc/lmod:/etc/lmod:ro); fi
fi
while IFS= read -r name; do
  case "$name" in
    SLURM_*|OMPI_*|OPAL_*|PMIX_*|PMI_*|PRTE_*|UCX_*|NCCL_*|CUDA_*|FI_*|OMP_*|DP_*|OPENBLAS_NUM_THREADS|MKL_NUM_THREADS|TF_NUM_INTRAOP_THREADS|TF_NUM_INTEROP_THREADS)
      args+=(--env "$name=${!name}") ;;
  esac
done < <(compgen -e)
if [[ "$implementation" == baseline ]]; then
  exec "${args[@]}" "$image" /usr/bin/bash --noprofile --norc -c \
    'driver=${LD_LIBRARY_PATH:-}; source /control/md_environment.sh "$1"; shift; site=$MD_SYSTEM_DEEPMD/lib/python3.13/site-packages; export LD_LIBRARY_PATH="$driver:$site/tensorflow:$site/torch/lib:$MD_SYSTEM_DEEPMD/lib:${LD_LIBRARY_PATH:-}"; unset PYTHONPATH; exec "$@"' \
    bash "$target" "${program_argv[@]}" "$@"
else
  exec "${args[@]}" "$image" /usr/bin/bash --noprofile --norc -c \
    'driver=${LD_LIBRARY_PATH:-}; source "$1"; shift; export LD_LIBRARY_PATH="$driver${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"; exec "$@"' bash \
    "/opt/software/lammps/$SAI_MD_VERSION/$target/share/sai/runtime-env.sh" "${program_argv[@]}" "$@"
fi
