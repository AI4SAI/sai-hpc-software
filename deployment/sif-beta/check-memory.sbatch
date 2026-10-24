#!/usr/bin/env bash
# Internal acceptance driver, not a user job template or an /opt payload file.
# Submit with explicit project account, partition, resources, chdir and output.
#SBATCH --time=00:20:00
set -eo pipefail
package=${1:?numerical check package}
bundle=${2:?candidate SIF bundle}
output=${3:?prepared project check directory}
[[ $SLURM_JOB_ACCOUNT == proj-sai-hpc-software && $output == /org/org-open-sai/proj-sai-hpc-software/* ]]
source /etc/profile.d/lmod.sh
unset TMPDIR TMP TEMP APPTAINER_TMPDIR APPTAINER_CACHEDIR APPTAINER_CONFIGDIR
export SAI_SIF_TRACE_DIR="$output/trace"
printf '%s\n' "$SLURM_JOB_ID" > "$output/job.id"
scontrol show job -o "$SLURM_JOB_ID" > "$output/allocation.txt"
if [[ $SLURM_JOB_PARTITION == DSPRHBM ]]; then
  MAP_OPT=slot:pe=$SLURM_CPUS_PER_TASK
  threads=$SLURM_CPUS_PER_TASK
else
  source /opt/sai/runtime/mps_mapping.d/${SLURM_JOB_PARTITION}.bash
  [[ $RANKS_PER_GPU == 1 ]]
  threads=$((CORES_PER_GPU/RANKS_PER_GPU))
fi
mpi=(mpirun -np "$SLURM_NTASKS" --map-by "$MAP_OPT")
load_app() {
  module purge
  module use "$bundle/modulefiles"
  module load "$1"
  [[ -z ${TMPDIR+x} ]]
  export OMP_NUM_THREADS=$threads OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
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
  export OMP_NUM_THREADS=2
  cd "$output/lammps"
  command -v mpirun > mpi.txt
  "${mpi[@]}" lmp -pk gpu "$SLURM_GPUS_ON_NODE" split 1.0 -in in.lmp -log log.lammps > stdout.txt 2>&1
  "${mpi[@]}" lmp -h > version.txt 2>&1
  load_app deepmd-kit-sif/master-20261024-g558f56962775
  cd "$output/deepmd"
  dp --version > version.txt 2>&1
  sed "s@os.environ\['SAI_CHECK_PREFIX'\]@'$SAI_SIF_DEEPMD_KIT_PREFIX'@" check.py > execute.py
  deepmd-python execute.py > stdout.txt 2>&1
  load_app gpumd-sif/master-20261024-g2c93346fce44
  cd "$output/gpumd"
  CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES%%,*}" gpumd > stdout.txt 2>&1
fi
module purge
[[ -z ${TMPDIR+x} ]]
cd "$output"
python3 -B "$package/check.py" verify "$output"
python3 - "$output" <<'PY'
import json, os, sys
from pathlib import Path
root = Path(sys.argv[1])
report = json.loads((root / 'verification.json').read_text())
traces = [json.loads(p.read_text()) for p in (root / 'trace').glob('*.json')]
for row in traces:
    assert row['uid'] == os.getuid()
    assert row['memory_root'] == f"/dev/shm/sai-{os.getuid()}-{report['job']}-0"
    assert row['memory_scope']['job'] == report['job']
    assert row['memory_scope']['schema'] == 1
    if row['software'] in ('abacus', 'cp2k', 'lammps'):
        assert '--sharens' in row['command']
    assert ('--nv' in row['command']) == (report['partition'] != 'DSPRHBM')
report.update(automatic_memory=True, caller_tmpdir_unchanged=True)
(root / 'verification.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
print('Private memory and numerical checks passed:', report['partition'])
PY
