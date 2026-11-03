#!/bin/bash
#SBATCH --job-name=fuse2fs-managed-probe
#SBATCH --account=proj-sai-hpc-software
#SBATCH --chdir=/org/org-open-sai/proj-sai-hpc-software/diagnostics/fuse2fs-workflow-20261103
#SBATCH --partition=DSPRHBM
#SBATCH --qos=rush-cpu
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=10
#SBATCH --output=/org/org-open-sai/proj-sai-hpc-software/diagnostics/fuse2fs-workflow-20261103/pilot-%j.log
exec /usr/bin/python3 -B /org/org-open-sai/proj-sai-hpc-software/diagnostics/fuse2fs-workflow-20261103/pilot.py /org/org-open-sai/proj-sai-hpc-software/diagnostics/fuse2fs-workflow-20261103/probe-a
