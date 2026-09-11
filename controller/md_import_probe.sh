#!/usr/bin/env bash
# Bounded diagnostic only: do not modify site packages or claim inference.
set -euo pipefail
[[ ( $# == 1 || $# == 2 ) && -d /.singularity.d ]] || { echo 'allocated contained import probe required' >&2; exit 2; }
[[ $# == 1 || "$2" =~ ^[0-9a-f]{40}$ ]] || exit 2
case "$1" in 4v100-avx512|16v100-avx2|8v100v0-avx512) ;; *) exit 2 ;; esac
[[ ${SAI_MD_ALLOCATED_JOB:-} =~ ^[0-9]+$ && -n ${SAI_MD_ALLOCATED_NODE:-} ]] || exit 2
[[ ${SAI_MD_ALLOCATED_NODE%%.*} == "$(uname -n | cut -d. -f1)" ]] || exit 2
[[ ! -e /workspace/import-probe && ! -L /workspace/import-probe ]]
mkdir -p /workspace/tmp /workspace/cache /workspace/import-probe
export TMPDIR=/workspace/tmp XDG_CACHE_HOME=/workspace/cache
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
source /control/md_environment.sh "$1"
site=$MD_SYSTEM_DEEPMD/lib/python3.13/site-packages
export LD_LIBRARY_PATH="$site/tensorflow:$site/torch/lib:$MD_SYSTEM_DEEPMD/lib:${LD_LIBRARY_PATH:-}"
export CUDA_VISIBLE_DEVICES='' PYTHONFAULTHANDLER=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export TF_NUM_INTRAOP_THREADS=1 TF_NUM_INTEROP_THREADS=1
export DP_INTRA_OP_PARALLELISM_THREADS=1 DP_INTER_OP_PARALLELISM_THREADS=1
unset PYTHONPATH DEVICE LOCAL_RANK
ulimit -c 0
cd /workspace/import-probe
"$MD_SYSTEM_DEEPMD/bin/python" - "${2:-}" <<'PROBE_PY'
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, '/control')
from md_science import file_digest, parse_reference, require_execution_context
require_execution_context()
source_sha = sys.argv[1]
cases = ([('triton', 'tensorflow', 'torch'), ('torch', 'triton', 'tensorflow')]
         if source_sha else [('triton',), ('torch', 'triton'), ('tensorflow', 'torch', 'triton'),
                             ('torch', 'tensorflow', 'triton'), ('deepmd.pt.model.descriptor',)])
rows = []

def capture(label, command, **extra):
    try:
        child = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=40, check=False)
        raw, status = child.stdout, child.returncode
    except subprocess.TimeoutExpired as error:
        raw, status = error.stdout or b'', 'timeout'
    Path(f'{label}.log').write_bytes(raw)
    row = dict(command=command, returncode=status, stdout_sha256=hashlib.sha256(raw).hexdigest(), **extra)
    rows.append(row)
    print('MD_IMPORT_CASE ' + json.dumps(row), flush=True)
    print(raw.decode(errors='replace'), flush=True)
    return status

for index, modules in enumerate(cases):
    code = ('import importlib\n' + ''.join(
        f'print("IMPORT {name}", flush=True)\nimportlib.import_module({name!r})\n'
        for name in modules) + 'print("IMPORT_SEQUENCE_OK", flush=True)\n')
    command = [sys.executable, '-X', 'faulthandler', '-c', code]
    capture(f'import-{index}', command, modules=modules)
conversion = None
if source_sha:
    # Extract only two immutable fixture blobs INSIDE the overlay. Never import
    # the upstream test module, download, or change the installed environment.
    for upstream, local in [('source/tests/infer/deeppot.pbtxt', 'deeppot.pbtxt'),
                            ('source/lmp/tests/test_lammps.py', 'oracle.py')]:
        data = subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null',
                '--git-dir=/input/deepmd-kit', 'show', source_sha + ':' + upstream], timeout=20)
        Path(local).write_bytes(data)
    reference = parse_reference(Path('oracle.py').read_text())
    conversion = {'source_sha': source_sha, 'source_graph_sha256': file_digest(Path('deeppot.pbtxt')),
                  'source_reference_sha256': file_digest(Path('oracle.py')), 'reference': reference['reference']}
    status = capture('convert-from', [sys.executable, '-X', 'faulthandler', '-m', 'deepmd',
                     'convert-from', 'pbtxt', '-i', 'deeppot.pbtxt', '-o', 'model.pb'],
                     input_sha256=conversion['source_graph_sha256'])
    if status == 0:
        conversion['model_pb_sha256'] = file_digest(Path('model.pb'))
        status = capture('convert-pt-preload-triton', [sys.executable, '-X', 'faulthandler', '-c',
            'import triton; import runpy; runpy.run_module("deepmd", run_name="__main__")',
            'convert-backend', 'model.pb', 'model.pth'], input_sha256=conversion['model_pb_sha256'],
            preload=['triton'])
        if status == 0:
            conversion['model_pth_sha256'] = file_digest(Path('model.pth'))
report = {'job': os.environ['SAI_MD_ALLOCATED_JOB'], 'node': os.uname().nodename,
          'cuda_visible_devices': os.environ.get('CUDA_VISIBLE_DEVICES'), 'cases': rows,
          'conversion': conversion,
          'scientific_verified': False, 'candidate_verified': False, 'published': False}
Path('summary.json').write_text(json.dumps(report, sort_keys=True) + '\n')
print('MD_IMPORT_DIAGNOSTIC_JSON ' + json.dumps(report, sort_keys=True), flush=True)
raise SystemExit(0 if all(row['returncode'] == 0 for row in rows) else 1)
PROBE_PY
