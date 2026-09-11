#!/usr/bin/env bash
# Trusted entry point: the host renderer provides a file-backed overlay,
# read-only /input/deepmd-kit bare cache, /control and a verified allocation.
# This proves only the preinstalled baseline, never candidate acceptance/speed.
set -euo pipefail
[[ $# == 2 ]] || { echo 'usage: md_baseline.sh TARGET DEEPMD_SHA' >&2; exit 2; }
target=$1
sha=$2
case "$target" in
  4v100-avx512|16v100-avx2|8v100v0-avx512) ;;
  *) echo 'unvalidated MD baseline target' >&2; exit 2 ;;
esac
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo 'a full DeepMD commit SHA is required' >&2; exit 2; }
[[ -d /.singularity.d ]] || { echo 'baseline must run inside the allocated overlay container' >&2; exit 2; }
[[ ${SAI_MD_ALLOCATED_JOB:-} =~ ^[0-9]+$ && -n ${SAI_MD_ALLOCATED_NODE:-} ]] || {
  echo 'trusted host Slurm allocation attestations are required' >&2; exit 2;
}
[[ ${SAI_MD_ALLOCATED_NODE%%.*} == "$(uname -n | cut -d. -f1)" ]] || {
  echo 'container is outside the attested compute node' >&2; exit 2;
}
[[ ! -L /workspace && ! -e /workspace/source && ! -L /workspace/source ]]
mkdir -p /workspace
[[ ! -e /workspace/baseline && ! -L /workspace/baseline ]]
export TMPDIR=/workspace/tmp
export XDG_CACHE_HOME=/workspace/cache
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
mkdir -p "$TMPDIR" "$XDG_CACHE_HOME"
source /control/md_environment.sh "$target"
site=$MD_SYSTEM_DEEPMD/lib/python3.13/site-packages
export LD_LIBRARY_PATH="$site/tensorflow:$site/torch/lib:$MD_SYSTEM_DEEPMD/lib:${LD_LIBRARY_PATH:-}"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export TF_NUM_INTRAOP_THREADS=1 TF_NUM_INTEROP_THREADS=1
export DP_INTRA_OP_PARALLELISM_THREADS=1 DP_INTER_OP_PARALLELISM_THREADS=1
export DP_BACKEND=tensorflow
unset PYTHONPATH DEVICE LOCAL_RANK
python=$MD_SYSTEM_DEEPMD/bin/python
"$python" - <<'GUARD_PY'
import sys
sys.path.insert(0, '/control')
from md_science import require_execution_context
require_execution_context()
GUARD_PY

out=/workspace/baseline
mkdir "$out"
mkdir "$out/logs" "$out/records"
phase=checkout
trap 'status=$?; printf "MD_BASELINE_FAILED phase=%s exit=%s; raw logs remain in /workspace/baseline/logs\n" "$phase" "$status" >&2; exit "$status"' ERR
logged() {
  phase=$1
  shift
  "$@" 2>&1 | tee "$out/logs/$phase.log"
}
# Never fetch or execute the checkout's Python/hooks. Upstream files are only
# data for md_science's AST allowlist and graph converter. All trees stay inside
# the overlay; the bare cache itself is read-only and no host /tmp is used.
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
export GIT_TERMINAL_PROMPT=0 GIT_ALLOW_PROTOCOL=file
[[ $(git --git-dir=/input/deepmd-kit rev-parse --is-bare-repository) == true ]]
git --git-dir=/input/deepmd-kit cat-file -e "$sha^{commit}"
logged checkout-clone git -c core.hooksPath=/dev/null clone --no-hardlinks --no-checkout \
  /input/deepmd-kit /workspace/source
logged checkout-revision git -c core.hooksPath=/dev/null -C /workspace/source checkout --detach "$sha"
[[ $(git -C /workspace/source rev-parse HEAD) == "$sha" ]]
git -C /workspace/source rev-parse HEAD > "$out/source.sha"
cd "$out"
module -t list > "$out/logs/modules.txt" 2>&1

logged devices "$python" - <<'DEVICES_PY'
import json
from pathlib import Path
import sys
sys.path.insert(0, '/control')
from md_science import require_execution_context
require_execution_context()
import tensorflow as tf
import torch
import jax
from deepmd.pt.utils.env import DEVICE
assert torch.cuda.is_available(), 'allocated GPU is not visible to site PyTorch'
assert 'sm_70' in torch.cuda.get_arch_list(), 'site PyTorch lacks V100 support'
assert DEVICE.type == 'cuda', 'DeepMD PyTorch inference selected CPU unexpectedly'
devices = {'tf': [str(item) for item in tf.config.list_physical_devices()],
           'pt': str(DEVICE), 'jax': [str(item) for item in jax.devices()]}
Path('devices.json').write_text(json.dumps(devices, sort_keys=True) + '\n')
print(json.dumps(devices, sort_keys=True))
DEVICES_PY

# The SAME upstream TF graph is converted for all three backends. A conversion
# failure must stop with its original log, not substitute an unrelated model.
logged prepare "$python" /control/md_science.py prepare /workspace/source "$out/case" \
  --backends tf pt jax
resources='{"nodes":1,"ranks":1,"allocated_gpus":1,"threads_per_rank":1}'
for backend in tf pt jax; do
  logged "python-$backend" "$python" /control/md_science.py python-eval "$out/case" \
    --backend "$backend" --implementation baseline --resources-json "$resources" \
    --repeats 3 --records "$out/records/python-$backend.json"
  logged "lammps-$backend" "$python" /control/md_science.py lammps-run "$out/case" \
    --backend "$backend" --implementation baseline --resources-json "$resources" \
    --repeats 3 --records "$out/records/lammps-$backend.json" \
    --executable "$MD_SYSTEM_LAMMPS/bin/lmp" --output-dir "$out/lammps-$backend"
done

phase=summary
"$python" - "$out" "$target" "$sha" <<'SUMMARY_PY'
import json
import math
import os
from pathlib import Path
import re
import sys
sys.path.insert(0, '/control')
from md_science import BACKENDS, TOLERANCES, _compare_numeric, _load_fixture, require_execution_context


def summarize(root, target, sha, job, node):
    """Recheck all baseline evidence; never invent a candidate comparison."""
    root = Path(root)
    if not re.fullmatch(r'[0-9a-f]{40}', sha) or (root / 'source.sha').read_text().strip() != sha:
        raise ValueError('source revision mismatch')
    if not re.fullmatch(r'[0-9]+', job) or not node:
        raise ValueError('missing allocation identity')
    groups = {}
    expected_resources = {'nodes': 1, 'ranks': 1, 'allocated_gpus': 1, 'threads_per_rank': 1}
    for backend in BACKENDS:
        fixture = _load_fixture(root / 'case', backend)
        if (fixture['required_backends'] != list(BACKENDS)
                or fixture['prepared_backends'] != list(BACKENDS)
                or fixture['tolerances'] != TOLERANCES):
            raise ValueError('baseline fixture must cover all backends with approved tolerances')
        for engine in ('python', 'lammps'):
            rows = json.loads((root / 'records' / f'{engine}-{backend}.json').read_text())
            if not isinstance(rows, list) or len(rows) != 4:
                raise ValueError('baseline requires one warmup and three numerical runs')
            for index, record in enumerate(rows):
                if (record.get('implementation') != 'baseline' or record.get('backend') != backend
                        or record.get('engine') != engine or record.get('node', '').split('.')[0] != node.split('.')[0]
                        or record.get('resources') != expected_resources
                        or record.get('warmup') is not (index == 0)
                        or record.get('input_sha256') != fixture['models'][backend]['input_sha256']
                        or record.get('reference') != fixture['reference']
                        or record.get('tolerances') != TOLERANCES):
                    raise ValueError('baseline record identity or fixture mismatch')
                seconds = record.get('seconds')
                if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
                    raise ValueError('invalid baseline timing')
                for observable, tolerance in TOLERANCES.items():
                    _compare_numeric(record['observables'][observable], fixture['reference'][observable],
                                     **tolerance, path=observable)
                if engine == 'lammps' and record.get('plumed', {}).get('passed') is not True:
                    raise ValueError('baseline LAMMPS lacks successful PLUMED execution')
            groups[f'{backend}/{engine}'] = rows
    return {'schema': 1, 'implementation': 'baseline', 'target': target,
            'source_sha': sha, 'job': job, 'node': node, 'records': groups,
            'backend_devices': json.loads((root / 'devices.json').read_text()),
            'baseline_scientific_verified': True, 'candidate_scientific_verified': False,
            'representative_performance_verified': False, 'published': False,
            'timing_note': 'Six-atom correctness fixture only; not representative performance.'}


if __name__ == '__main__':
    require_execution_context()
    root, target, sha = sys.argv[1:]
    result = summarize(root, target, sha, os.environ['SAI_MD_ALLOCATED_JOB'], os.environ['SAI_MD_ALLOCATED_NODE'])
    with (Path(root) / 'summary.json').open('x') as stream:
        json.dump(result, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')
    print('MD_SCIENCE_BASELINE_JSON ' + json.dumps(result, sort_keys=True, allow_nan=False))
SUMMARY_PY
