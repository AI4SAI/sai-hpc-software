#!/usr/bin/env bash
#SBATCH --nodes=2
#SBATCH --ntasks=8
#SBATCH --ntasks-per-node=4
#SBATCH --gpus-per-node=4
#SBATCH --time=04:00:00
#SBATCH --export=ALL
set -eo pipefail
run=${1:?frozen experiment directory}
export PATH=/usr/bin:/bin LD_LIBRARY_PATH= LD_PRELOAD= PYTHONDONTWRITEBYTECODE=1
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
export TMPDIR=/home/stardust/sai-hpc-software/runtime/com-${SLURM_JOB_ID:?}
export SAI_MULTI_RUNTIME=$TMPDIR
export CUDA_CACHE_PATH=$TMPDIR/cuda-cache XDG_CACHE_HOME=$TMPDIR/cache
export APPTAINER_TMPDIR=$TMPDIR APPTAINER_CACHEDIR=$TMPDIR/apptainer-cache
mkdir -p "$TMPDIR" "$CUDA_CACHE_PATH" "$XDG_CACHE_HOME" "$APPTAINER_CACHEDIR"
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/apps /opt/modules/modulefiles/devtools
module load cp2k/2026.2-auto apptainer/1.4.4
cd "$run"
source /opt/sai_config/mps_mapping.d/16V100.bash
[[ $MAP_OPT == ppr:4:node:pe=4 && $RANKS_PER_GPU == 1 ]]
if [[ -n ${NCCL_TOPO_FILE:-} ]]; then
    cp -- "$NCCL_TOPO_FILE" results/nccl-topology.xml
    export NCCL_TOPO_FILE=$run/results/nccl-topology.xml
fi
export OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 OMP_DYNAMIC=FALSE
export OMP_PLACES=cores OMP_PROC_BIND=close SLURM_EXPORT_ENV=ALL
export PRTE_MCA_prte_tmpdir_base=$TMPDIR OMPI_MCA_orte_tmpdir_base=$TMPDIR
export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher
set -u
id > results/account.txt
module -t list > results/modules.txt 2>&1
scontrol show job -o "$SLURM_JOB_ID" > results/allocation.txt
scontrol show hostnames "$SLURM_JOB_NODELIST" > results/nodes.txt
apptainer version > results/apptainer-version.txt
mpirun --version > results/mpi-version.txt
srun --nodes=2 --ntasks=2 --ntasks-per-node=1 --gpus-per-node=4 --gpu-bind=none \
    /usr/bin/python3 "$run/control/deployment/cp2k-container-overhead/multinode_rank.py" \
    inventory "$run/inventory" > results/inventory.log 2>&1
python3 "$run/control/deployment/cp2k-container-overhead/multinode.py" execute "$run"
