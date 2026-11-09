#!/bin/bash
#SBATCH --job-name=sai-hdf5-installed
#SBATCH --partition=DSPRHBM
#SBATCH --qos=rush-cpu
#SBATCH --nodes=1
#SBATCH --ntasks=2
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=10
#SBATCH --export=ALL
#SBATCH --output=/org/org-open-sai/proj-sai-hpc-software/runs/qe-hdf5-installed-check-20261108-r3/results/slurm-%j.log
#SBATCH --account=proj-sai-hpc-software
#SBATCH --chdir=/org/org-open-sai/proj-sai-hpc-software/runs/qe-hdf5-installed-check-20261108-r3
set -eo pipefail
umask 077
export TMPDIR=/org/org-open-sai/proj-sai-hpc-software/runs/qe-hdf5-installed-check-20261108-r3/runtime
export APPTAINER_TMPDIR="$TMPDIR" XDG_CONFIG_HOME="$TMPDIR/config" XDG_CACHE_HOME="$TMPDIR/cache"
mkdir -p "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME"
source /org/org-open-sai/proj-sai-hpc-software/controller/app-dependencies-20261108-r2/qe_environment.sh dsprhbm
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
cd /org/org-open-sai/proj-sai-hpc-software/runs/qe-hdf5-installed-check-20261108-r3
hostname > results/hostname.txt
module -t list > results/modules.txt 2>&1
env | sort > results/environment.txt
lscpu > results/cpu.txt
nvfortran --version > results/compiler.txt
mpifort --showme > results/mpi-wrapper.txt
findmnt -T /opt/apps/qe/dependencies/hdf5/1.14.6-nvhpc26.3-ompi5.0.10-f90/DSPRHBM > results/provider-mount.txt
test -z "${APPTAINER_CONTAINER:-}"
python3 -B check_inventory.py /org/org-open-sai/proj-sai-hpc-software/controller/app-dependencies-20261108-r2 /org/org-open-sai/proj-sai-hpc-software/runs/qe-hdf5-installed-check-20261108-r3 /opt/apps/qe/dependencies/hdf5/1.14.6-nvhpc26.3-ompi5.0.10-f90/DSPRHBM
/opt/devtools/cmake/3.31.6/bin/cmake -S source -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER="$(command -v mpicc)" -DCMAKE_Fortran_COMPILER="$(command -v mpifort)" -DINSTALLED_HDF5_PREFIX=/opt/apps/qe/dependencies/hdf5/1.14.6-nvhpc26.3-ompi5.0.10-f90/DSPRHBM > results/configure.log 2>&1
/opt/devtools/cmake/3.31.6/bin/cmake --build build --parallel 2 --verbose > results/build.log 2>&1
ldd build/consumer > results/ldd.txt
! grep -q "not found" results/ldd.txt
readelf -d build/consumer > results/dynamic.txt
sha256sum build/consumer > results/consumer.sha256
"$MPI_HOME/bin/mpiexec" --bind-to none -n 2 ./build/consumer > results/roundtrip.log 2>&1
grep -q SAI_HDF5_MPI_ROUNDTRIP_PASS results/roundtrip.log
cp build/provider-targets.txt results/provider-targets.txt
cp build/CMakeFiles/consumer.dir/link.txt results/link.txt
echo PHYSICAL_HDF5_CONSUMER_PASS
