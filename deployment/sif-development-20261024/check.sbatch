#!/usr/bin/env bash
# Submit with explicit account, partition, resources, chdir and output path.
# GPU allocations intentionally omit --cpus-per-task.
#SBATCH --time=00:20:00
#SBATCH --export=ALL
set -eo pipefail
package=${1:?package directory}
bundle=${2:?SIF module bundle}
output=${3:?prepared check directory}
[[ $SLURM_JOB_ACCOUNT == proj-sai-hpc-software && $output == /org/org-open-sai/proj-sai-hpc-software/* ]]
export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD="" PYTHONPATH=""
export TMPDIR="$output/tmp" TMP="$output/tmp" TEMP="$output/tmp"
export SLURM_EXPORT_ENV=ALL
export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher
export SAI_SIF_TRACE_DIR="$output/trace"
source /etc/profile.d/lmod.sh
printf '%s\n' "$SLURM_JOB_ID" > "$output/job.id"
scontrol show job -o "$SLURM_JOB_ID" > "$output/allocation.txt"
mapfile -t nodes < <(scontrol show hostnames "$SLURM_JOB_NODELIST")
if [[ ${#nodes[@]} == 2 ]]; then
  hosts="${nodes[0]}:2,${nodes[1]}:2"; mapping=ppr:2:node:PE=1
else
  hosts="${nodes[0]}:2"; mapping=ppr:2:node:PE=1
fi
mpi=(mpirun -np "$SLURM_NTASKS" --host "$hosts" --map-by "$mapping" --bind-to core)
load_app() {
  module purge
  module use "$bundle/modulefiles"
  module load "$1"
  export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
  export DP_INTRA_OP_PARALLELISM_THREADS=1 DP_INTER_OP_PARALLELISM_THREADS=1
  export TF_NUM_INTRAOP_THREADS=1 TF_NUM_INTEROP_THREADS=1
  module -t list > "$output/${1%%/*}-modules.txt" 2>&1
}
load_app cp2k-sif/master-20261024-gcfe5e5d87aaf
cd "$output/cp2k"
command -v mpirun > mpi.txt
/usr/bin/time -f '%e' -o elapsed.txt "${mpi[@]}" cp2k.psmp -i input.inp > stdout.txt 2>&1
"${mpi[@]}" cp2k.psmp --version > version.txt 2>&1
load_app abacus-sif/develop-20261024-g370ba685e670
cd "$output/abacus"
command -v mpirun > mpi.txt
/usr/bin/time -f '%e' -o elapsed.txt "${mpi[@]}" abacus > stdout.txt 2>&1
if [[ $SLURM_JOB_PARTITION != DSPRHBM ]]; then
  load_app lammps-sif/develop-20261024-ga0e2bd6efb3d
  cd "$output/lammps"
  command -v mpirun > mpi.txt
  "${mpi[@]}" lmp -pk gpu 1 split 1.0 -in in.lmp -log log.lammps > stdout.txt 2>&1
  "${mpi[@]}" lmp -h > version.txt 2>&1
  load_app deepmd-kit-sif/master-20261024-g558f56962775
  cd "$output/deepmd"
  dp --version > version.txt 2>&1
  # This value is only a check input, passed as a literal script argument.
  sed "s@os.environ\['SAI_CHECK_PREFIX'\]@'$SAI_SIF_DEEPMD_KIT_PREFIX'@" check.py > execute.py
  deepmd-python execute.py > stdout.txt 2>&1
  load_app gpumd-sif/master-20261024-g2c93346fce44
  cd "$output/gpumd"
  gpumd > stdout.txt 2>&1
fi
module purge
cd "$output"
python3 -B "$package/check.py" verify "$output"
