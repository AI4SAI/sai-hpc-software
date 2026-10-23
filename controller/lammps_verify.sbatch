#!/usr/bin/env bash
#SBATCH --nodes=2
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=1
#SBATCH --time=00:40:00
#SBATCH --export=NIL
set -eo pipefail
export HOME=@HOME@
export USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?}
export SAI_SOFTWARE_ROOT=@PROJECT@
export SAI_LAMMPS_ACCEPTANCE_TASK=${1:?acceptance task directory required}
output=$SAI_LAMMPS_ACCEPTANCE_TASK
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export LD_LIBRARY_PATH="" LD_PRELOAD="" PYTHONPATH=""
unset PYTHONHOME SAI_MD_ALLOCATED_JOB SAI_MD_ALLOCATED_NODE
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
export TMPDIR=@RUNTIME@
export TMP="$TMPDIR" TEMP="$TMPDIR" XDG_RUNTIME_DIR="$TMPDIR"
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/devtools
module load apptainer/1.4.4 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto
set -u
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export DP_INTRA_OP_PARALLELISM_THREADS=1 DP_INTER_OP_PARALLELISM_THREADS=1
export TF_NUM_INTRAOP_THREADS=1 TF_NUM_INTEROP_THREADS=1
export SLURM_EXPORT_ENV=ALL
export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher
module -t list > "$output/modules.txt" 2>&1
scontrol show job -o "$SLURM_JOB_ID" > "$output/allocation.txt"
scontrol show hostnames "$SLURM_JOB_NODELIST" > "$output/nodes.txt"
/usr/bin/python3 -B "$output/control/lammps_acceptance.py" run "$output"
