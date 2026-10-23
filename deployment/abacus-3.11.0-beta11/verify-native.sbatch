#!/usr/bin/env bash
#SBATCH --nodes=2
#SBATCH --time=00:40:00
#SBATCH --export=ALL
# Per-partition CPU/GPU requests are supplied by the submission script.
set -eo pipefail
output=${1:?native result directory required}
work_root=${2:?writable home or SAI project work root required}
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export LD_LIBRARY_PATH="" LD_PRELOAD="" PYTHONPATH=""
unset PYTHONHOME
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 LMOD_IGNORE_CACHE=1
export TMPDIR="$work_root/runtime/an-${SLURM_JOB_ID:?}"
export TMP="$TMPDIR" TEMP="$TMPDIR" XDG_RUNTIME_DIR="$TMPDIR"
export CUDA_CACHE_PATH="$output/cuda-cache" XDG_CACHE_HOME="$output/cache"
mkdir -p "$TMPDIR" "$CUDA_CACHE_PATH" "$XDG_CACHE_HOME"
chmod 700 "$TMPDIR"
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/devtools /opt/modules/modulefiles/apps
module load abacus/3.11.0-beta11-auto
set -u
export OMP_NUM_THREADS=1 OMP_PROC_BIND=false OMP_PLACES=cores
if [[ "$SLURM_JOB_PARTITION" == DSPRHBM ]]; then
    export OMP_NUM_THREADS=2 OMP_PROC_BIND=true
fi
export OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export SLURM_EXPORT_ENV=ALL
export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher
export NCCL_DEBUG=TRACE NCCL_DEBUG_SUBSYS=INIT,COLL
export CUSOLVERMP_LOG_LEVEL=5 CUSOLVERMP_LOG_MASK=31
unset NCCL_DEBUG_FILE CUSOLVERMP_LOG_FILE
module -t list > "$output/modules.txt" 2>&1
scontrol show job -o "$SLURM_JOB_ID" > "$output/allocation.txt"
scontrol show hostnames "$SLURM_JOB_NODELIST" > "$output/nodes.txt"
command -v abacus > "$output/binary.txt"
command -v mpirun > "$output/mpirun.txt"
/usr/bin/python3 -B "$output/control/native_checks.py" run "$output"
module unload abacus/3.11.0-beta11-auto
[[ -z "${SAI_ABACUS_PREFIX:-}" && -z "${SAI_ABACUS_NATIVE_PARTITION:-}" && -z "${ABACUS_ROOT:-}" ]]
printf 'ABACUS_NATIVE_MODULE_UNLOADED\n' > "$output/module-unloaded.txt"
