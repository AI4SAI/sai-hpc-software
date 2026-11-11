#!/usr/bin/env python3
"""Check installed NepTrainKit CPU/CUDA prediction against upstream reference data."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys

import numpy as np
from NepTrainKit.core.calculator import Nep3Calculator
from NepTrainKit.core.structure import Structure
from NepTrainKit.core.types import NepBackend
import nep_adapters

case, output = map(Path, sys.argv[1:])
assert importlib.metadata.version('NepTrainKit') == '3.1.5'
assert importlib.metadata.version('nep-adapters') == '1.0.5'
prefix = Path(os.environ.get('NEPTRAINKIT_EXPECT_PREFIX', '/opt/apps/neptrainkit/3.1.5'))
import NepTrainKit
assert Path(NepTrainKit.__file__).is_relative_to(prefix)
assert Path(nep_adapters.__file__).is_relative_to(prefix)
status = nep_adapters.backend_status('cuda')
assert status.installed and status.available, status
structures = Structure.read_multiple(case / 'train.xyz')[:1]
cpu = Nep3Calculator(case / 'nep.txt', backend=NepBackend.CPU)
cuda = Nep3Calculator(case / 'nep.txt', backend=NepBackend.CUDA)
assert cuda.backend == NepBackend.CUDA
c, g = cpu.predict(structures), cuda.predict(structures)
reference = {name: np.load(case / (name + '.npy')) for name in ('energy', 'forces', 'virial')}
np.testing.assert_allclose(c.energy, reference['energy'], rtol=0, atol=5e-4)
np.testing.assert_allclose(c.forces, reference['forces'], rtol=0, atol=5e-6)
np.testing.assert_allclose(c.structure_virials[:, [0,4,8,1,5,6]], reference['virial'], rtol=0, atol=5e-4)
errors = {}
for field in ('energy', 'forces', 'structure_virials'):
    expected, observed = getattr(c, field), getattr(g, field)
    assert np.all(np.isfinite(observed))
    np.testing.assert_allclose(observed, expected, rtol=1e-6, atol=2e-6)
    errors[field] = float(np.max(np.abs(observed-expected)))
cpu_desc = cpu.descriptors(structures)
gpu_desc = cuda.descriptors(structures)
np.testing.assert_allclose(gpu_desc, cpu_desc, rtol=1e-6, atol=2e-6)
errors['descriptors'] = float(np.max(np.abs(gpu_desc-cpu_desc)))
record = dict(passed=True, version='3.1.5', backend_version='1.0.5',
              implementation=str(NepTrainKit.__file__), cuda_status=str(status),
              gpu_mask=os.environ.get('CUDA_VISIBLE_DEVICES'),
              frames=len(structures), atoms=sum(len(s) for s in structures),
              cpu_cuda_max_absolute_error=errors,
              reference_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in case.iterdir() if p.is_file()})
output.write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record, indent=2))
