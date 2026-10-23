#!/usr/bin/env bash
#SBATCH --job-name=cp2k-libxc-16v100-avx2
#SBATCH --partition=16V100
#SBATCH --qos=flood-1o2gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=30
#SBATCH --export=NIL
#SBATCH --output=/org/org-open-sai/proj-sai-hpc-software/runtime-tests/cp2k-libxc-39983165813-16v100-avx2-prep2/results/slurm-%j.log
#SBATCH --account=proj-sai-hpc-software
#SBATCH --chdir=/org/org-open-sai/proj-sai-hpc-software/runtime-tests/cp2k-libxc-39983165813-16v100-avx2-prep2
#SBATCH --gpus-per-node=1
set -eo pipefail
umask 077
export HOME=/org/org-open-sai/proj-sai-hpc-software/runtime-tests/cp2k-libxc-39983165813-16v100-avx2-prep2
export USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?}
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export LD_LIBRARY_PATH="" LD_PRELOAD="" PYTHONDONTWRITEBYTECODE=1
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
export SAI_SOFTWARE_ROOT=/org/org-open-sai/proj-sai-hpc-software
export SAI_CP2K_IMAGE=/org/org-open-sai/proj-sai-hpc-software/containers/software/cp2k/development/master-2026-10-23-gcfe5e5d87aaf-rae6467ac2934/16V100/39983165813-1-development-16v100-avx2-2026-10-23-cfe5e5d87aaf.sif
export TMPDIR=/org/org-open-sai/proj-sai-hpc-software/runtime/lx-73e2d758a40a
mkdir -p "$TMPDIR"
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/devtools /opt/modules/modulefiles/apps
module load apptainer/1.4.4 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto
source /opt/sai_config/mps_mapping.d/16V100.bash
set -u
export OMP_NUM_THREADS=1 OMP_PLACES=cores OMP_PROC_BIND=close SLURM_EXPORT_ENV=ALL
export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher
scontrol show job -o "$SLURM_JOB_ID" > results/allocation.txt
module -t list > results/modules.txt 2>&1
hostname -f > results/node.txt
python3 /org/org-open-sai/proj-sai-hpc-software/runtime-tests/cp2k-libxc-39983165813-16v100-avx2-prep2/libxc_smoke.py execute /org/org-open-sai/proj-sai-hpc-software/runtime-tests/cp2k-libxc-39983165813-16v100-avx2-prep2
