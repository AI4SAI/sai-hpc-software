#!/bin/bash
# Internal evidence driver; run.sbatch is the unchanged public torchrun template.
#SBATCH --time=00:10:00
set -eo pipefail
export SAI_SIF_TRACE_DIR="$PWD/trace"
export TMPDIR="$PWD/.sai-sif-$SLURM_JOB_ID"
mkdir -p "$TMPDIR"
scontrol show job -o "$SLURM_JOB_ID" > allocation.txt
printf '%s\n' "$SLURM_JOB_ID" > job.id
module purge
module load deepmd-kit/master-beta-later
deepmd-python deepmd-prepare.py
deepmd-python -m torch.distributed.run --standalone --nproc_per_node=2 nccl-check.py > nccl.log 2>&1
/usr/bin/time -f '%e' -o elapsed.txt bash -x run.sbatch > training.log 2>&1
deepmd-python -c 'import json,numpy as np; from pathlib import Path; a=np.loadtxt("lcurve.out", ndmin=2); assert len(a)>=2 and np.isfinite(a).all(); ranks=[json.loads(Path(f"nccl-rank{i}.json").read_text()) for i in range(2)]; Path("verification.json").write_text(json.dumps(dict(passed=True,nccl=ranks,learning_curve=a.tolist()),indent=2)+"\n")'
