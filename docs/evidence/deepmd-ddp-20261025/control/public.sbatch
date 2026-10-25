#!/bin/bash
# Internal acceptance of the published alias, not a user template.
#SBATCH --time=00:05:00
set -eo pipefail
source /etc/profile.d/lmod.sh
module purge
module load deepmd-kit/master-beta-later
module list > modules.txt 2>&1
printf '%s\n' "$SAI_SIF_DEEPMD_KIT_IMAGE" "$SAI_SIF_DEEPMD_KIT_BETA_GENERATION" > selection.txt
mkdir -p trace
export SAI_SIF_TRACE_DIR="$PWD/trace"
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export DP_INTRA_OP_PARALLELISM_THREADS=1 DP_INTER_OP_PARALLELISM_THREADS=1
deepmd-python -m torch.distributed.run --standalone --nproc_per_node=2 -m deepmd --pt train input.json > training.log 2>&1
