"""Small inference check; retained with evidence, never installed publicly."""
import json
import os
from pathlib import Path
import numpy as np
import torch
import deepmd
from deepmd.infer import DeepPot
from deepmd.pt.utils.env import DEVICE

fixture = json.loads(Path('fixture.json').read_text())
assert deepmd.__file__.startswith('/opt/apps/deepmd-kit/deepmd-kit-development-master-2026-10-23-g558f56962775-re04065e624bd/16V100' + '/')
assert torch.cuda.is_available() and DEVICE.type == 'cuda'
model = DeepPot('model.pth')
energy, forces, virial = model.eval(np.array(fixture['coordinates']).reshape(1, -1),
                                  np.array(fixture['box']).reshape(1, 9), fixture['atom_types'])
errors = {}
for name, value in [('energy', energy), ('forces', forces), ('virial', virial)]:
    expected = np.asarray(fixture['reference'][name]).reshape(np.asarray(value).shape)
    np.testing.assert_allclose(value, expected, atol=1e-7, rtol=1e-5)
    errors[name] = float(np.max(np.abs(value - expected)))
assert torch.cuda.memory_allocated() > 0
Path('result.json').write_text(json.dumps(dict(passed=True, errors=errors,
    package=deepmd.__file__, device=str(DEVICE), cuda_bytes=torch.cuda.memory_allocated()), indent=2) + '\n')
