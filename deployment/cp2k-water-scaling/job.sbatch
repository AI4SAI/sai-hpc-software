#!/usr/bin/env bash
#SBATCH --nodes=1
#SBATCH --export=ALL
set -eo pipefail
run=${1:?frozen experiment}
export PATH=/usr/bin:/bin LD_LIBRARY_PATH= LD_PRELOAD= PYTHONDONTWRITEBYTECODE=1
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/devtools /opt/modules/modulefiles/apps
module load cp2k/2026.2-auto
set -u
export TMPDIR=$run/runtime CUDA_CACHE_PATH=$run/runtime/cuda-cache XDG_CACHE_HOME=$run/runtime/cache
export PRTE_MCA_prte_tmpdir_base=$TMPDIR OMPI_MCA_orte_tmpdir_base=$TMPDIR
export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher SLURM_EXPORT_ENV=ALL
mkdir -p "$TMPDIR" "$CUDA_CACHE_PATH" "$XDG_CACHE_HOME"
cd "$run"
scontrol show job -o "$SLURM_JOB_ID" > results/allocation.txt
lscpu > results/lscpu.txt
nvidia-smi topo -m > results/gpu-topology.txt
module -t list > results/modules.txt 2>&1
python3 "$run/control/deployment/cp2k-water-scaling/run.py" execute "$run"
