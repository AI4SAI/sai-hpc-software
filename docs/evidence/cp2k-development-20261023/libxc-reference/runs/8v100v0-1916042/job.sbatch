#!/usr/bin/env bash
#SBATCH --job-name=cp2k-libxc-ref-8v100v0-avx512
#SBATCH --partition=8V100V0
#SBATCH --qos=rush-1o2gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=15
#SBATCH --export=NIL
#SBATCH --output=/org/org-open-sai/proj-sai-hpc-software/runtime-tests/cp2k-libxc-reference-v1-39983165813-8v100v0-avx512/results/slurm-%j.stdout
#SBATCH --error=/org/org-open-sai/proj-sai-hpc-software/runtime-tests/cp2k-libxc-reference-v1-39983165813-8v100v0-avx512/results/slurm-%j.stderr
#SBATCH --account=proj-sai-hpc-software
#SBATCH --chdir=/org/org-open-sai/proj-sai-hpc-software/runtime-tests/cp2k-libxc-reference-v1-39983165813-8v100v0-avx512
#SBATCH --gpus-per-node=1
set -eo pipefail
umask 077
export HOME=/org/org-open-sai/proj-sai-hpc-software/runtime-tests/cp2k-libxc-reference-v1-39983165813-8v100v0-avx512
export USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?}
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export LD_LIBRARY_PATH="" LD_PRELOAD="" PYTHONDONTWRITEBYTECODE=1
unset PYTHONPATH PYTHONHOME APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
export SAI_SOFTWARE_ROOT=/org/org-open-sai/proj-sai-hpc-software
export SAI_CP2K_IMAGE=/org/org-open-sai/proj-sai-hpc-software/containers/software/cp2k/development/master-2026-10-23-gcfe5e5d87aaf-rae6467ac2934/8V100V0/39983165813-1-development-8v100v0-avx512-2026-10-23-cfe5e5d87aaf-node-retry1.sif
export TMPDIR=/org/org-open-sai/proj-sai-hpc-software/runtime/lr-385b1f823b47
mkdir -p "$TMPDIR"
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/devtools /opt/modules/modulefiles/apps
module load apptainer/1.4.4 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto
source /opt/sai_config/mps_mapping.d/8V100V0.bash
set -u
export OMP_NUM_THREADS=1 SLURM_EXPORT_ENV=ALL
scontrol show job -o "$SLURM_JOB_ID" > results/allocation.txt
module -t list > results/modules.txt 2>&1
hostname -f > results/node.txt
/usr/bin/python3 -B /org/org-open-sai/proj-sai-hpc-software/runtime-tests/cp2k-libxc-reference-v1-39983165813-8v100v0-avx512/run_reference.py execute /org/org-open-sai/proj-sai-hpc-software/runtime-tests/cp2k-libxc-reference-v1-39983165813-8v100v0-avx512
