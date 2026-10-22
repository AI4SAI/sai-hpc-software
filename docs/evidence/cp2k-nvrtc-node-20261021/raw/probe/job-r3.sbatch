#!/usr/bin/env bash
#SBATCH --partition=4V100
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --time=00:20:00
#SBATCH --qos=rush-1o2gpu
#SBATCH --export=ALL
set -eo pipefail
base=${1:?directory containing frozen probe and fixture files}
library_dir=${2:-}
run=$base/job-$SLURM_JOB_ID
mkdir -p "$run/runtime/cuda-cache" "$run/runtime/cache"
export TMPDIR=$run/runtime CUDA_CACHE_PATH=$run/runtime/cuda-cache XDG_CACHE_HOME=$run/runtime/cache
export PATH=/usr/bin:/bin LD_LIBRARY_PATH= LD_PRELOAD= PYTHONDONTWRITEBYTECODE=1
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/devtools /opt/modules/modulefiles/apps
module load cp2k/2026.2-auto
if [[ -n $library_dir ]]; then
    export LD_LIBRARY_PATH=$library_dir:$LD_LIBRARY_PATH
fi
source /opt/sai/runtime/mps_mapping.d/4V100.bash
set -u
export OMP_NUM_THREADS=4 OMP_PLACES=cores OMP_PROC_BIND=close
export PRTE_MCA_prte_tmpdir_base=$TMPDIR OMPI_MCA_orte_tmpdir_base=$TMPDIR
export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher
export SLURM_EXPORT_ENV=ALL
ulimit -c 0
cd "$run"
hostname > hostname.txt
scontrol show job -o "$SLURM_JOB_ID" > allocation.txt
module -t list > modules.txt 2>&1
ldd "$(command -v cp2k.psmp)" > linkage.txt
lscpu > cpu.txt
nvidia-smi > nvidia-smi.txt
cat /proc/driver/nvidia/version > driver.txt
cat /proc/sys/vm/mmap_rnd_bits > mmap-rnd-bits.txt 2>&1 || true
ulimit -a > limits.txt
printf '%s\n' "$LD_LIBRARY_PATH" > library-path.txt
set +e
timeout 120 python3 "$base/probe/nvrtc_probe.py" \
    --dbcsr-source "$base/probe/dbcsr-source" > nvrtc.jsonl 2> nvrtc.err
printf '%s\n' "$?" > nvrtc.exit
set -e
for case in water343-original water2-gpu water2-noacc; do
    mkdir "$run/$case"
    cp "$base/$case.inp" "$run/$case/input.inp"
    cd "$run/$case"
    unset DBCSR_RUN_ON_GPU
    if [[ $case == *-noacc ]]; then export DBCSR_RUN_ON_GPU=0; fi
    set +e
    timeout --signal=TERM --kill-after=10 240 mpirun -np 1 --map-by "$MAP_OPT" \
        --report-bindings cp2k.psmp -i input.inp -o output.log > launch.log 2>&1
    status=$?
    set -e
    printf '%s\n' "$status" > exit.txt
done
