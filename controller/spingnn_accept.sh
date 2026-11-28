#!/bin/bash
# The same science runs on the final read-only SIF and its native export.
set -eo pipefail
prefix=$1
case_dir=$2
control=$3
site_script=$4
context=$5
if [[ $context == container ]]; then
  test -n "${APPTAINER_CONTAINER:?final SIF required}"
  export OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1
elif [[ $context == exported-native ]]; then
  test -z "${APPTAINER_CONTAINER:-}"
else
  exit 2
fi
export PATH=/usr/bin:/bin LD_LIBRARY_PATH='' LD_PRELOAD=''
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/devtools
source "$site_script"
set -u
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher
export OMPI_CC="$CC" OMPI_CXX="$CXX" NVCC_WRAPPER_DEFAULT_COMPILER="$CXX"
export PYTHONDONTWRITEBYTECODE=1 SAI_SPINGNN_CONTEXT="$context"
mkdir -p "$case_dir/modulefiles/spingnn"
cp "$prefix/share/sai/fixture/"* "$case_dir/"
/usr/bin/python3 -c 'import json,sys; sys.path.insert(0,sys.argv[1]); from spingnn_release import module_text; from pathlib import Path; p=Path(sys.argv[2]); print(module_text(json.loads((p/"share/sai/release-identity.json").read_text()),p))' \
  "$control" "$prefix" > "$case_dir/modulefiles/spingnn/check.lua"
module use "$case_dir/modulefiles"
module load spingnn/check
test "$(command -v lmp)" = "$prefix/bin/lmp"
test -L "$prefix/bin/lmp"
test "$(readlink "$prefix/bin/lmp")" = lmp_spingnn2
test "$(readlink -f "$prefix/bin/lmp")" = "$prefix/bin/lmp_spingnn2"
unset PYTHONPATH
cd "$case_dir"
module -t list > modules.txt 2>&1
ldd "$prefix/bin/lmp_spingnn2" > lammps-ldd.txt
ldd "$SPINGNN2_LIBRARY" > native-ldd.txt
if grep -Eiq 'not found|libtorch|libc10' native-ldd.txt lammps-ldd.txt; then exit 1; fi
if [[ $SAI_SPINGNN_BACKEND == cpu ]] && grep -Eiq 'libcuda|libcudart' native-ldd.txt lammps-ldd.txt; then exit 1; fi
if [[ $SAI_SPINGNN_BACKEND == cuda ]]; then
  nvidia-smi -q > nvidia-smi.txt
  cuobjdump --list-elf "$SPINGNN2_LIBRARY" > native-cubins.txt
  cuobjdump --list-elf "$prefix/bin/lmp_spingnn2" > lammps-cubins.txt
  nsys profile --trace=cuda,nvtx --sample=none --cpuctxsw=none --output=cuda-trace \
    python "$control/spingnn_science.py" > science.log 2>&1
  nsys stats --report cuda_gpu_kern_sum,cuda_api_sum --format csv cuda-trace.nsys-rep > cuda-kernels.csv 2>nsys-stats.log
  cuobjdump --list-elf kernel-*.cubin > jit-cubin.txt
else
  python "$control/spingnn_science.py" > science.log 2>&1
fi
# Unload must use the stored identity even after the allocation env disappears.
unset SLURM_JOB_ID SLURM_JOB_PARTITION
module unload spingnn/check
test -z "${SPINGNN2_ROOT:-}" && test -z "${SPINGNN2_LIBRARY:-}"
printf 'passed\n' > module-unload.txt
