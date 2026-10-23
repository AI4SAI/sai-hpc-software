#!/usr/bin/env bash
#SBATCH --job-name=cp2k-mount-ro-1914241
#SBATCH --partition=4V100
#SBATCH --qos=rush-1o2gpu
#SBATCH --nodes=1
#SBATCH --nodelist=4v100n01
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --time=15
#SBATCH --export=NIL
#SBATCH --account=proj-sai-hpc-software
#SBATCH --chdir=/org/org-open-sai/proj-sai-hpc-software/runs/cp2k-mount-ro-1914241-20261024
#SBATCH --output=/org/org-open-sai/proj-sai-hpc-software/runs/cp2k-mount-ro-1914241-20261024/results/slurm-%j.log
set -eo pipefail
umask 077
export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD="" PYTHONDONTWRITEBYTECODE=1
export HOME=/org/org-open-sai/proj-sai-hpc-software/runs/cp2k-mount-ro-1914241-20261024/runtime
export TMPDIR=/org/org-open-sai/proj-sai-hpc-software/runs/cp2k-mount-ro-1914241-20261024/runtime APPTAINER_TMPDIR=/org/org-open-sai/proj-sai-hpc-software/runs/cp2k-mount-ro-1914241-20261024/runtime APPTAINER_CACHEDIR=/org/org-open-sai/proj-sai-hpc-software/runs/cp2k-mount-ro-1914241-20261024/apptainer-cache
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
source /etc/profile.d/lmod.sh
module load apptainer/1.4.4
scontrol show job -o "$SLURM_JOB_ID" > results/allocation.txt
python3 /org/org-open-sai/proj-sai-hpc-software/runs/cp2k-mount-ro-1914241-20261024/mount_probe.py /org/org-open-sai/proj-sai-hpc-software/runs/cp2k-mount-ro-1914241-20261024
