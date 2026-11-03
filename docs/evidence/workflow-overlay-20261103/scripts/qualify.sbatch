#!/bin/bash
#SBATCH --job-name=workflow-overlay-qualify
#SBATCH --account=proj-sai-hpc-software
#SBATCH --chdir=/org/org-open-sai/proj-sai-hpc-software/diagnostics/fuse2fs-workflow-20261103
#SBATCH --partition=DSPRHBM
#SBATCH --qos=rush-cpu
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=30
#SBATCH --output=/org/org-open-sai/proj-sai-hpc-software/diagnostics/fuse2fs-workflow-20261103/qualify-%j.log
exec /usr/bin/python3 -B /org/org-open-sai/proj-sai-hpc-software/diagnostics/fuse2fs-workflow-20261103/qualify.py
