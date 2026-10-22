#!/usr/bin/env bash
#SBATCH --job-name=cp2k-dependency-probe
#SBATCH --partition=4V100
#SBATCH --qos=flood-1o2gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --time=120
#SBATCH --export=NIL
set -eo pipefail
export HOME=/home/stardust
export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""
source /etc/profile.d/lmod.sh
module load cp2k/2026.1-cuda12.9-sm70-auto
cp2k.psmp --version
module load apptainer/1.4.4 cmake/3.31.6
set -u
root=$HOME/sai-hpc-software
run=$root/runs/cp2k-dependency-$SLURM_JOB_ID
mkdir -p "$run/runtime" "$run/cache"
export TMPDIR=$run/runtime APPTAINER_TMPDIR=$run/runtime APPTAINER_CACHEDIR=$run/cache
apptainer overlay create --fakeroot --sparse --size 8192 "$run/work.ext3"
apptainer exec --fakeroot --cleanenv --containall --no-home \
  --no-mount bind-paths,home,cwd,tmp,hostfs --pwd / --net --network none --nv \
  --bind /usr:/usr:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro \
  --bind /opt/devtools:/opt/devtools:ro --bind /opt/modules:/opt/modules:ro \
  --bind /etc/profile.d/lmod.sh:/etc/profile.d/lmod.sh:ro \
  --bind "$root/cache/cp2k-probe:/input:ro" \
  --overlay "$run/work.ext3" "$root/containers/base/minimal-v1.sif" \
  /usr/bin/bash -ec '
    export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""
    source /etc/profile.d/lmod.sh
    module use /opt/modules/modulefiles/devtools
    module load cmake/3.31.6 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto cuda/12.9.1 saiblas/2603-gnu-auto
    module load nvmplibs/26.7-tmp
    mkdir -p /workspace/tmp /workspace/fypp
    export TMPDIR=/workspace/tmp
    tar --no-same-owner -xzf /input/dbcsr-latest.tar.gz -C /workspace
    tar --no-same-owner -xzf /input/cp2k-latest.tar.gz -C /workspace
    unzip -q /input/fypp-3.2-py3-none-any.whl -d /workspace/fypp
    python3 /workspace/fypp/fypp.py --version
    cmake -S /workspace/dbcsr-latest -B /workspace/dbcsr-build \
      -DUSE_MPI=ON -DUSE_ACCEL=cuda -DWITH_GPU=V100 \
      -DCMAKE_CUDA_ARCHITECTURES=70 -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_CXX_FLAGS=-Wno-error=deprecated-declarations \
      -DFYPP_EXECUTABLE=/workspace/fypp/fypp.py \
      -DCMAKE_INSTALL_PREFIX=/opt/software/cp2k-dependencies/dbcsr
    cmake --build /workspace/dbcsr-build --parallel 8
    cmake --install /workspace/dbcsr-build
    cmake -S /workspace/cp2k-latest -B /workspace/cp2k-build \
      -DCMAKE_BUILD_TYPE=Release -DCP2K_USE_MPI=ON \
      -DCP2K_USE_ACCEL=CUDA -DCP2K_WITH_GPU=V100 \
      -DCP2K_USE_CUDA=ON -DCP2K_USE_DBCSR_CONFIG=ON \
      -DDBCSR_DIR=/opt/software/cp2k-dependencies/dbcsr/lib64/cmake/dbcsr \
      -DCMAKE_CUDA_ARCHITECTURES=70 -DCP2K_USE_CUSOLVER_MP=ON \
      -DCP2K_CUSOLVER_MP_ROOT=/opt/devtools/nvidia/mp_libs \
      -DCP2K_NCCL_ROOT=/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/comm_libs/12.9/nccl-2.29 \
      -DCMAKE_INSTALL_PREFIX=/opt/software/cp2k/latest-v100
    cmake --build /workspace/cp2k-build --parallel 8
    cmake --install /workspace/cp2k-build
    export LD_LIBRARY_PATH=/opt/software/cp2k/latest-v100/lib64:/opt/software/cp2k/latest-v100/lib:${LD_LIBRARY_PATH:-}
    /opt/software/cp2k/latest-v100/bin/cp2k.psmp --version
    echo CP2K_CUDA_BUILD_COMPLETE
    echo DBCSR_CUDA_MPI_BUILD_COMPLETE
  '
