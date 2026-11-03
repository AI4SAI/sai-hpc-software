#!/bin/bash
#SBATCH --job-name=apptainer-sai-verify
#SBATCH --account=proj-sai-hpc-software
#SBATCH --chdir=/org/org-open-sai/proj-sai-hpc-software
#SBATCH --ntasks=1
#SBATCH --time=20
#SBATCH --output=/org/org-open-sai/proj-sai-hpc-software/diagnostics/apptainer-sai-verify-%j.log
exec /usr/bin/python3 -B /home/stardust/sai-hpc-software/deployment/apptainer-1.4.4-sai/verify.py \
  --apptainer /opt/devtools/apptainer-sai/1.4.4/bin/apptainer \
  --site-config /opt/devtools/apptainer-sai/1.4.4/etc/apptainer/apptainer.conf \
  --work "/org/org-open-sai/proj-sai-hpc-software/diagnostics/apptainer-sai-installed-${SLURM_JOB_ID}"
