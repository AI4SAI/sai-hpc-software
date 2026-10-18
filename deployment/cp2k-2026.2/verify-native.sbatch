#!/usr/bin/env bash
#SBATCH --nodes=2
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=1
#SBATCH --time=00:40:00
#SBATCH --export=ALL
set -eo pipefail
package_dir=${1:?deployment package directory required}
project_root=${2:?project directory required}
output=${3:?result directory required}
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export LD_LIBRARY_PATH="" LD_PRELOAD=""
source /etc/profile.d/lmod.sh
module purge
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
module use /opt/modules/modulefiles/devtools /opt/modules/modulefiles/apps
module load cp2k/2026.2-auto
case "${SLURM_JOB_PARTITION:?}" in
  DSPRHBM) export MAP_OPT=ppr:1:node:pe=2 OMP_NUM_THREADS=2 ;;
  4V100|16V100|8V100V0)
    source "/opt/sai_config/mps_mapping.d/$SLURM_JOB_PARTITION.bash"
    export OMP_NUM_THREADS=1 ;;
  *) exit 2 ;;
esac
set -u
export OMP_PLACES=cores OMP_PROC_BIND=close SLURM_EXPORT_ENV=ALL
export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher
export PYTHONDONTWRITEBYTECODE=1
# Short home path also avoids exceeding MPI Unix socket pathname limits.
export TMPDIR="$project_root/runtime/cn-${SLURM_JOB_ID:?}"
export CUDA_CACHE_PATH="$output/cuda-cache" XDG_CACHE_HOME="$output/cache"
mkdir -p "$TMPDIR" "$CUDA_CACHE_PATH" "$XDG_CACHE_HOME"
module -t list > "$output/modules.txt" 2>&1
scontrol show job -o "$SLURM_JOB_ID" > "$output/allocation.txt"
scontrol show hostnames "$SLURM_JOB_NODELIST" > "$output/nodes.txt"
command -v cp2k.psmp > "$output/binary.txt"
command -v mpirun > "$output/mpirun.txt"
mpirun --version > "$output/mpi-version.txt"
/usr/bin/python3 "$package_dir/run-native-checks.py" "$project_root" "$output"
