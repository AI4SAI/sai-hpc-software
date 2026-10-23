#!/usr/bin/env bash
# Each host MPI rank enters the candidate SIF; scratch stays on the project filesystem.
set -euo pipefail
output=${SAI_LAMMPS_ACCEPTANCE_TASK:?}
program=${1:?}; shift
settings=$(/usr/bin/python3 -B "$output/control/acceptance.py" guard "$output")
mapfile -t settings <<< "$settings"
lmp_prefix=${settings[0]}; dp_prefix=${settings[1]}; image=${settings[2]}
case "$program" in
  lmp) argv=("$lmp_prefix/bin/lmp" "$@") ;;
  inventory) argv=("$dp_prefix/bin/python" -B /acceptance/control/acceptance.py inventory /acceptance) ;;
  *) exit 2 ;;
esac
work=$(pwd -P)
[[ "$work" == "$output/results/"* ]]
runtime="${TMPDIR:?}/$(hostname -s)"
mkdir -p "$runtime" "$runtime/cache" "$runtime/cuda"
export APPTAINER_TMPDIR="$runtime" APPTAINER_CACHEDIR="$runtime/cache"
args=(apptainer exec --nv --cleanenv --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd /work)
for path in /usr /lib /lib64 /opt/devtools \
  /opt/apps/conda_env/deepmd-kit-3.2.0 /opt/apps/conda_env/VeloxChem/gpu \
  /opt/apps/plumed/plumed-2.10.1 "$dp_prefix"; do
  args+=(--bind "$path:$path:ro")
done
args+=(--bind "$output:/acceptance:ro" --bind "$work:/work:rw"
       --bind "$TMPDIR:$TMPDIR:rw" --bind "$runtime:/tmp:rw"
       --env "TMPDIR=$runtime" --env "TMP=$runtime" --env "TEMP=$runtime"
       --env "CUDA_CACHE_PATH=$runtime/cuda" --env "XDG_CACHE_HOME=$runtime/cache"
       --env PYTHONDONTWRITEBYTECODE=1 --env PYTHONNOUSERSITE=1
       --env "SAI_MD_ALLOCATED_JOB=$SLURM_JOB_ID" --env "SAI_MD_ALLOCATED_NODE=$(hostname)"
       --env "SAI_LMP_PREFIX=$lmp_prefix" --env "SAI_LMP_IMAGE=$image"
       --env "SAI_LMP_PROGRAM=$program")
while IFS= read -r name; do
  case "$name" in
    SLURM_*|OMPI_*|OPAL_*|PMIX_*|PMI_*|PRTE_*|UCX_*|NCCL_*|CUDA_VISIBLE_DEVICES|FI_*|OMP_*|DP_*|OPENBLAS_NUM_THREADS|MKL_NUM_THREADS|TF_NUM_INTRAOP_THREADS|TF_NUM_INTEROP_THREADS)
      args+=(--env "$name=${!name}") ;;
  esac
done < <(compgen -e)
exec "${args[@]}" "$image" /usr/bin/bash --noprofile --norc -c '
  set -euo pipefail
  driver=${LD_LIBRARY_PATH:-}
  source "$SAI_LMP_PREFIX/share/sai/runtime-env.sh"
  export LD_LIBRARY_PATH="$driver${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  [[ -n "${CUDA_VISIBLE_DEVICES:-}" && "$CUDA_VISIBLE_DEVICES" != -1 ]]
  if [[ "$SAI_LMP_PROGRAM" == lmp ]]; then
    rank=${OMPI_COMM_WORLD_RANK:?}
    binary="$SAI_LMP_PREFIX/bin/lmp"
    cpu=
    while read -r name value; do
      [[ "$name" != Cpus_allowed_list: ]] || cpu=$value
    done < /proc/self/status
    [[ -n "$cpu" ]]
    printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\n" "$rank" "$(hostname -s)" "$binary" \
      "$CUDA_VISIBLE_DEVICES" "$cpu" \
      "$(sha256sum "$binary" | cut -d " " -f 1)" "$SAI_LMP_IMAGE" > "/work/ranks/rank-$rank.tsv"
    if [[ "${2:-}" == -h ]]; then
      ldd "$binary" > "/work/ranks/ldd-$rank.txt"
      if grep -q "not found" "/work/ranks/ldd-$rank.txt"; then exit 1; fi
    fi
  fi
  exec "$@"
' bash "${argv[@]}"
