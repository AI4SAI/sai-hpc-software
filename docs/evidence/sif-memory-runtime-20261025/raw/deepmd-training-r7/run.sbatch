#!/bin/bash
#SBATCH --time=00:04:00
set -eo pipefail
source /etc/profile.d/lmod.sh
module purge
unset TMPDIR TMP TEMP APPTAINER_TMPDIR APPTAINER_CACHEDIR APPTAINER_CONFIGDIR
module use /org/org-open-sai/proj-sai-hpc-software/runtime-tests/sif-memory-runtime-20261024/bundle-r7/modulefiles
module load deepmd-kit-sif/master-20261024-g558f56962775
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export DP_INTRA_OP_PARALLELISM_THREADS=1 DP_INTER_OP_PARALLELISM_THREADS=1
export SAI_SIF_TRACE_DIR=/org/org-open-sai/proj-sai-hpc-software/runtime-tests/sif-memory-runtime-20261024/deepmd-training-r7/trace
dp --pt train input.json > stdout.txt 2>&1
python3 - <<'CHECK'
import json,math,os
from pathlib import Path
rows=[[float(v) for v in l.split()] for l in Path('lcurve.out').read_text().splitlines() if l.strip() and not l.startswith('#')]
assert [r[0] for r in rows]==[1,2] and all(math.isfinite(v) for r in rows for v in r)
assert Path('model.ckpt-2.pt').stat().st_size>0
traces=[json.loads(p.read_text()) for p in Path('trace').glob('*.json')]
assert traces and all('--nv' in t['command'] and t['memory_scope'] for t in traces)
Path('verification.json').write_text(json.dumps(dict(passed=True,job=os.environ['SLURM_JOB_ID'],partition=os.environ['SLURM_JOB_PARTITION'],steps=2,gpus=1,learning_curve=rows,automatic_memory=True),indent=2)+'\n')
CHECK
