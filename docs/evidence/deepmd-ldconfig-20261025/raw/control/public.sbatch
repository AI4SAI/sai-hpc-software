#!/bin/bash
#SBATCH --time=00:05:00
set -eo pipefail
source /etc/profile.d/lmod.sh
root="$PWD"
mkdir publication-check
cp -r control publication-check/control
cd publication-check
module purge
module load deepmd-kit/master-beta-later
module list > modules.txt 2>&1
command -v dp > launcher.txt
printf '%s\n' "$SAI_SIF_DEEPMD_KIT_BETA_GENERATION" > generation.txt
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export DP_INTRA_OP_PARALLELISM_THREADS=1 DP_INTER_OP_PARALLELISM_THREADS=1
mkdir trace
export SAI_SIF_TRACE_DIR="$PWD/trace"
deepmd-python - <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, str(Path.cwd() / 'control'))
import training_audit
training_audit.prepare(Path.cwd() / 'case', 'dpa2')
PY
cd case
dp --pt train input.json > training.log 2>&1
python3 - <<'PY'
import hashlib,json,math,os
from pathlib import Path
rows=[[float(v) for v in line.split()] for line in Path('lcurve.out').read_text().splitlines()
      if line.strip() and not line.startswith('#')]
assert [int(row[0]) for row in rows] == list(range(1,9))
assert all(math.isfinite(v) for row in rows for v in row)
checkpoint=Path('model.ckpt-8.pt')
assert checkpoint.stat().st_size > 0
traces=[json.loads(p.read_text()) for p in Path('../trace').glob('*.json')]
assert {t['program'] for t in traces} == {'dp','deepmd-python'}
assert all(t['memory_scope'] and '--nv' in t['command'] and '/usr/sbin:/sbin:ro' in t['command'] for t in traces)
assert Path('../generation.txt').read_text().strip() == '20261025-deepmd-ldconfig-r1'
Path('../verification.json').write_text(json.dumps(dict(passed=True,job=os.environ['SLURM_JOB_ID'],
 partition=os.environ['SLURM_JOB_PARTITION'],steps=8,learning_curve=rows,
 checkpoint_sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest()),indent=2)+'\n')
PY
