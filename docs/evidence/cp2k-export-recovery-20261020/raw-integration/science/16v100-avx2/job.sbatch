#!/usr/bin/env bash
#SBATCH --job-name=cp2k-science-39293908513-1-release-16v100-avx2-2026-10-19-67b5da876dd6-export-science
#SBATCH --partition=16V100
#SBATCH --qos=flood-1o2gpu
#SBATCH --nodes=2
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=1
#SBATCH --time=60
#SBATCH --export=NIL
#SBATCH --output=/home/stardust/sai-hpc-software/runtime-tests/39293908513-1-release-16v100-avx2-2026-10-19-67b5da876dd6-export-science/results/slurm-%j.log
#SBATCH --gpus-per-node=1
set -eo pipefail
umask 077
export HOME=/home/stardust
export USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?}
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export LD_LIBRARY_PATH="" LD_PRELOAD=""
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
export SAI_SOFTWARE_ROOT=/home/stardust/sai-hpc-software
export SAI_CP2K_VERSION=v2026.2
export SAI_CP2K_IMAGE=/home/stardust/sai-hpc-software/containers/software/cp2k/release/v2026.2-g67b5da876dd6-r38dfba7e3ed7/16V100/39293908513-1-release-16v100-avx2-2026-10-19-67b5da876dd6-export.sif
# Canonical delivery prefix: /opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-r38dfba7e3ed7/16V100
export TMPDIR=/home/stardust/sai-hpc-software/runtime/cs-11868650ad59
export APPTAINER_TMPDIR=/home/stardust/sai-hpc-software/runtime-tests/39293908513-1-release-16v100-avx2-2026-10-19-67b5da876dd6-export-science/apptainer-runtime
export APPTAINER_CACHEDIR=/home/stardust/sai-hpc-software/runtime-tests/39293908513-1-release-16v100-avx2-2026-10-19-67b5da876dd6-export-science/apptainer-cache
mkdir -p "$TMPDIR" "$APPTAINER_TMPDIR" "$APPTAINER_CACHEDIR"
source /etc/profile.d/lmod.sh
module purge
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
module use /opt/modules/modulefiles/devtools /opt/modules/modulefiles/apps
module load apptainer/1.4.4 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto
source /opt/sai_config/mps_mapping.d/16V100.bash
[[ "${MAP_OPT,,}" == ppr:1:node:pe=4 ]] || exit 2
set -u
export OMP_NUM_THREADS=1
export OMP_PLACES=cores OMP_PROC_BIND=close SLURM_EXPORT_ENV=ALL
export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher
export SAI_BENCH_BINARY=/home/stardust/sai-hpc-software/controller/c1f63becb1502cb324c0fe4816f9ec4187fb04a1/39293908513-1-release-16v100-avx2-2026-10-19-67b5da876dd6/cp2k
cd /home/stardust/sai-hpc-software/runtime-tests/39293908513-1-release-16v100-avx2-2026-10-19-67b5da876dd6-export-science
scontrol show job -o "$SLURM_JOB_ID" > results/allocation.txt
scontrol show hostnames "$SLURM_JOB_NODELIST" > results/nodes.txt
mapfile -t hosts < results/nodes.txt
[[ ${#hosts[@]} -eq 2 && "${hosts[0]}" != "${hosts[1]}" ]]
all_hosts="${hosts[0]},${hosts[1]}"
mkdir -p results/candidate-version/ranks results/candidate-version/resources
export SAI_CP2K_TRACE_DIR=/home/stardust/sai-hpc-software/runtime-tests/39293908513-1-release-16v100-avx2-2026-10-19-67b5da876dd6-export-science/results/candidate-version/ranks
export SAI_BENCH_RESOURCES=/home/stardust/sai-hpc-software/runtime-tests/39293908513-1-release-16v100-avx2-2026-10-19-67b5da876dd6-export-science/results/candidate-version/resources
readlink -f "$SAI_BENCH_BINARY" > results/candidate-version/binary.txt
sha256sum "$SAI_BENCH_BINARY" > results/candidate-version/binary.sha256
command -v mpirun > results/candidate-version/mpirun.txt
mpirun --version > results/candidate-version/mpi-version.txt
module -t list > results/candidate-version/modules.txt 2>&1
mpirun -np 2 --host "$all_hosts" --map-by "$MAP_OPT" --report-bindings ./rank-exec.sh --version > results/candidate-version/version.log 2>&1
run_one() {
  local case="$1" mode="$2" ranks="$3" selected="$all_hosts"
  [[ "$mode" != single ]] || selected="${hosts[0]}"
  local work="cases/$case/$mode"
  mkdir -p "cases/$case"
  mkdir "$work"
  cp -- "fixtures/$case/"* "$work/"
  mkdir "$work/ranks" "$work/resources"
  export SAI_CP2K_TRACE_DIR=/home/stardust/sai-hpc-software/runtime-tests/39293908513-1-release-16v100-avx2-2026-10-19-67b5da876dd6-export-science/$work/ranks
  export SAI_BENCH_RESOURCES=/home/stardust/sai-hpc-software/runtime-tests/39293908513-1-release-16v100-avx2-2026-10-19-67b5da876dd6-export-science/$work/resources
  printf "%s\t%s\n" "$case" "$mode" >> results/run-order.tsv
  printf "%s\n" "$MAP_OPT" > "$work/map.txt"
  readlink -f "$SAI_BENCH_BINARY" > "$work/binary.txt"
  sha256sum "$SAI_BENCH_BINARY" > "$work/binary.sha256"
  command -v mpirun > "$work/mpirun.txt"
  ( cd "$work"
    /usr/bin/time -f "%e" -o elapsed.txt mpirun -np "$ranks" --host "$selected" --map-by "$MAP_OPT" --report-bindings /home/stardust/sai-hpc-software/runtime-tests/39293908513-1-release-16v100-avx2-2026-10-19-67b5da876dd6-export-science/rank-exec.sh -i input.inp > cp2k.log 2>&1
  )
}
run_one water-gpw single 1
run_one water-gpw multi 2
run_one water-diag single 1
run_one water-diag multi 2
run_one water-elpa single 1
run_one water-elpa multi 2
run_one water-dftd4 single 1
run_one water-dftd4 multi 2
run_one ch2o-tblite single 1
run_one ch2o-tblite multi 2
run_one water64-gpw single 1
run_one water64-gpw multi 2
run_one water64-elpa single 1
run_one water64-elpa multi 2
echo CP2K_CANDIDATE_SCIENCE_FINISHED
