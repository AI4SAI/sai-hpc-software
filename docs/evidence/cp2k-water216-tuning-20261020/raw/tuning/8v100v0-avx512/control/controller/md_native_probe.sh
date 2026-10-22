#!/usr/bin/env bash
# Small allocated-node baseline probe, inside the same containment policy.
set -euo pipefail
source /control/md_environment.sh "$1"
mkdir -p /workspace/tmp
site=$MD_SYSTEM_DEEPMD/lib/python3.13/site-packages
export LD_LIBRARY_PATH="$site/tensorflow:$site/torch/lib:$MD_SYSTEM_DEEPMD/lib:${LD_LIBRARY_PATH:-}"
lscpu
module -t list 2>&1
"$MD_SYSTEM_LAMMPS/bin/lmp" -h
"$MD_SYSTEM_DEEPMD/bin/python" /control/md_probe.py --deepmd "$MD_SYSTEM_DEEPMD" \
  --lammps "$MD_SYSTEM_LAMMPS" > /workspace/site-inventory.json
"$MD_SYSTEM_DEEPMD/bin/python" - <<'PY'
import importlib.metadata as m
import json
import tensorflow as tf
import torch
import jax
from pathlib import Path
value = json.loads(Path('/workspace/site-inventory.json').read_text())
assert torch.cuda.is_available(), 'no allocated GPU available'
assert 'sm_70' in torch.cuda.get_arch_list(), 'site PyTorch lacks V100 code'
assert tf.sysconfig.CXX11_ABI_FLAG == int(torch.compiled_with_cxx11_abi()) == 1
x = torch.arange(32, dtype=torch.float64, device='cuda')
assert float(x.sum()) == 496.0
torch.cuda.synchronize()
summary = {
    'node': value['node'], 'packages': len(value['lammps']['packages']),
    'styles': sum(map(len, value['lammps']['styles'].values())),
    'backends': value['deepmd']['backends'], 'plumed': value['plumed']['version'],
    'plumed_features': value['plumed']['features'],
    'torch_cuda': torch.version.cuda, 'torch_arches': torch.cuda.get_arch_list(),
    'torch_gpu_kernel': True, 'tf_devices': [str(x) for x in tf.config.list_physical_devices()],
    'jax_devices': [str(x) for x in jax.devices()],
    'build_dependencies': {name: m.version(name) for name in ('scikit-build-core', 'packaging', 'dependency_groups')},
    'scientific_deepmd_inference_verified': False,
}
print('MD_NATIVE_BASELINE_JSON ' + json.dumps(summary, sort_keys=True))
PY
