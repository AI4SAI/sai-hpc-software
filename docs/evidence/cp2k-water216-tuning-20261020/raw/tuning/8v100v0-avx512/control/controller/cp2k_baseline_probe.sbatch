#!/usr/bin/env bash
# Diagnostic only: validate fixed benchmark inputs against the site GPU binary.
# This does not replace the two-node candidate/baseline publication benchmark.
#SBATCH --job-name=cp2k-baseline-input-probe
#SBATCH --partition=4V100
#SBATCH --qos=flood-1o2gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --time=5
#SBATCH --export=NIL
set -euo pipefail
root=/home/stardust/sai-hpc-software
run_id=${1:?unique diagnostic run id required}
control=${2:?immutable trusted controller path required}
shift 2
if (( $# == 0 )); then
  set -- water-gpw water-dftd4 ch2o-tblite water-elpa water64-gpw
fi
for case in "$@"; do
  case "$case" in
    water-gpw|water-dftd4|ch2o-tblite|water-elpa|water64-gpw) ;;
    *) echo "unknown diagnostic case: $case" >&2; exit 2 ;;
  esac
done
[[ "$run_id" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ && "$control" == "$root/controller/"* ]]
run="$root/runs/$run_id"
test -d "$run/results"
export HOME=/home/stardust USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?}
export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""
export TMPDIR="$root/runtime/cp2k-probes/$run_id/mpi"
mkdir -p "$TMPDIR"
source /etc/profile.d/lmod.sh
module purge
module load cp2k/2026.1-cuda12.9-sm70-auto
export OMP_NUM_THREADS=1 SLURM_EXPORT_ENV=ALL
export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher
cp2k.psmp --version > "$run/results/version.log"
module -t list > "$run/results/modules.txt" 2>&1
readlink -f "$(command -v cp2k.psmp)" > "$run/results/binary.txt"
PYTHONPATH="$control" python3 -c 'import cp2k_benchmark as b, sys; from pathlib import Path; b.stage_fixtures(Path(sys.argv[1]))' "$run"
for case in "$@"; do
  work="$run/cases/$case"
  mkdir -p "$work"
  cp -- "$run/fixtures/$case/baseline/"* "$work/"
  (
    cd "$work"
    /usr/bin/time -f '%e' -o elapsed.txt mpirun -np 1 --map-by ppr:1:node:pe=1 \
      cp2k.psmp -i input.inp > cp2k.log 2>&1
  )
  PYTHONPATH="$control" python3 -c 'import cp2k_benchmark as b, json, sys; from pathlib import Path; p=Path(sys.argv[1]); print(json.dumps(b.parse_science((p/"cp2k.log").read_text(), (p/"forces.xyz").read_text(), (p/"elapsed.txt").read_text(), sys.argv[2])))' \
    "$work" "$case" > "$run/results/$case.json"
done
echo CP2K_BASELINE_INPUTS_CHECKED_NOT_CANDIDATE_ACCEPTANCE
