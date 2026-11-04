#!/usr/bin/env bash
set -euo pipefail
target=${1:?}; prefix=${2:?}; sha=${3:?}
source /control/environment.sh "$target" cp2k
module unload gcc
module load gcc/14.3.0
source "$prefix/share/sai/runtime-env.sh"
if [[ -f "$prefix/share/sai/nccl-mpi-progress-fix.json" ]]; then
  cp "$prefix/share/sai/nccl-mpi-progress-fix.json" /results/nccl-mpi-progress-fix.json
fi
python3 /control/cp2k_feature_contract.py verify "$prefix" "$target" "$sha" \
  > /results/application-checks.log 2>&1
python3 /control/cp2k_devtools_application.py science "$prefix" /results/science
