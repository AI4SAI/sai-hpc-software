#!/usr/bin/env bash
# Check the administrator's actual Tcl module against the final SIF.
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --qos=flood-1o2gpu
#SBATCH --time=00:05:00
#SBATCH --export=NIL
set -euo pipefail
run=${1:?}; isa=${2:?}; package=${3:?}
export PATH=/usr/bin:/bin LD_LIBRARY_PATH= LD_PRELOAD=
name=2026.02.001-2603-gnu-auto
mkdir -p "$run/work/modulefiles/elpa"
cp "$package/$name" "$run/work/modulefiles/elpa/$name"
cat > "$run/work/check-module.sh" <<'SH'
set -eo pipefail
export LD_PRELOAD=
source /etc/profile.d/lmod.sh
module use /workspace/modulefiles /opt/modules/modulefiles/devtools
module load elpa/2026.02.001-2603-gnu-auto
set -u
[[ "$ELPA_ROOT" == /opt/devtools/elpa/elpa-2026.02.001-2603-gnu-$1/nvidia ]]
[[ "$MPI_HOME" == /opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-$1 ]]
[[ "$OPENBLAS_ROOT" == /opt/devtools/saiblas/2603-gnu-$1 ]]
set +u
module -t list 2>&1
set -u
elpa2_print_kernels_openmp > /workspace/verify/module-kernels.txt
ldd "$ELPA_ROOT/lib/libelpa_openmp.so" > /workspace/verify/module-ldd.txt
! grep -q 'not found' /workspace/verify/module-ldd.txt
sha256sum /workspace/modulefiles/elpa/2026.02.001-2603-gnu-auto > /workspace/verify/module.sha256
set +u
module unload elpa/2026.02.001-2603-gnu-auto
set -u
[[ -z ${ELPA_ROOT:-} && -z ${SAI_ELPA_2603_ISA:-} ]]
echo "ELPA_MODULE_LOAD_UNLOAD_PASS $1" | tee /workspace/verify/module-passed.txt
SH
bash "$run/control/container.sh" verify "$run" "$isa" bash /workspace/check-module.sh "$isa"
