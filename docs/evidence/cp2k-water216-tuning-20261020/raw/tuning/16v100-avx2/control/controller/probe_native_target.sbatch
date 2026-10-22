#!/usr/bin/env bash
# Submit with an explicit partition and --output below the project run root.
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --qos=flood-1o2gpu
#SBATCH --time=5
#SBATCH --export=NIL
set -eo pipefail
export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""
export USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?}
probe_root=/home/stardust/sai-hpc-software/runs/native-target-probe-20260911
export TMPDIR="$probe_root/runtime" APPTAINER_TMPDIR="$probe_root/runtime"
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/devtools
module load gcc/13.3.0 cmake/3.31.6 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto
module load cuda/12.9.1 nvmplibs/26.7-tmp saiblas/2603-gnu-auto libxc/7.0.0-auto elpa/2026.02.001-2603-gnu
set -u
hostname
lscpu
nvidia-smi -L
module -t list 2>&1
gcc -march=native -mtune=native -Q --help=target | grep -E 'march=|mtune=|mavx|msse'
printf 'MPI_HOME=%s\nOPENBLAS_ROOT=%s\nELPA_ROOT=%s\n' "${MPI_HOME:-}" "${OPENBLAS_ROOT:-}" "${ELPA_ROOT:-}"
mapping="/opt/sai_config/mps_mapping.d/$SLURM_JOB_PARTITION.bash"
if [[ -f "$mapping" ]]; then sed -n '1,180p' "$mapping"; else printf 'NO_SITE_MAPPING\n'; fi
printf 'NATIVE_TARGET_PROBED\n'
