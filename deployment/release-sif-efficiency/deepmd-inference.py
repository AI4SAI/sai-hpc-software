"""Synchronized GPU inference throughput, with a replicated force/energy oracle."""
import hashlib
import json
import os
from pathlib import Path
import time
import numpy as np
import torch
import deepmd
from deepmd.infer import DeepPot
from deepmd.pt.utils.env import DEVICE

fixture = json.loads(Path('fixture.json').read_text())
assert torch.cuda.is_available() and DEVICE.type == 'cuda'
offsets = np.array([(i, j, k) for i in range(7) for j in range(7) for k in range(7)]) * 13.0
coords = (np.asarray(fixture['coordinates'])[None, :, :] + offsets[:, None, :]).reshape(1, -1)
types = fixture['atom_types'] * len(offsets)
box = np.asarray(fixture['box']).reshape(1, 9) * 7
model = DeepPot('model.pth')


def evaluate():
    return model.eval(coords, box, types)


observed = evaluate()
expected = [np.asarray(fixture['reference']['energy']) * 343,
            np.tile(fixture['reference']['forces'], (343, 1)),
            np.asarray(fixture['reference']['virial']) * 343]
errors = {}
for name, a, b in zip(('energy', 'forces', 'virial'), observed, expected):
    b = b.reshape(a.shape)
    np.testing.assert_allclose(a, b, atol=1e-7, rtol=1e-5)
    errors[name] = float(np.max(np.abs(a - b)))
torch.cuda.synchronize()
count = int(Path('evaluations.txt').read_text()) if Path('evaluations.txt').exists() else 100
begin = time.perf_counter()
for _ in range(count):
    observed = evaluate()
torch.cuda.synchronize()
elapsed = time.perf_counter() - begin
package = Path(deepmd.__file__)
Path('inference.json').write_text(json.dumps(dict(passed=True, atoms=len(types), evaluations=count,
    compute_seconds=elapsed, package=str(package), package_sha256=hashlib.sha256(package.read_bytes()).hexdigest(),
    package_device=package.stat().st_dev, device=str(DEVICE), cuda_bytes=torch.cuda.memory_allocated(),
    errors=errors, affinity=sorted(os.sched_getaffinity(0))), indent=2) + '\n')
