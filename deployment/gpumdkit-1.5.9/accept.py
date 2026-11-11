#!/usr/bin/env python3
"""Exercise GPUMDkit conversions, NEP prediction and plotting with fixed inputs."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import numpy as np
from ase import Atoms
from ase.io import read, write

prefix, fixtures, output = map(Path, sys.argv[1:])
output.mkdir(parents=True)
env = dict(os.environ, MPLBACKEND='Agg', MPLCONFIGDIR=str(output / '.matplotlib'),
           NUMBA_CACHE_DIR=str(output / '.numba'), PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1',
           OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
checks = {}


def command(name, args, cwd):
    result = subprocess.run([str(prefix / 'bin/gpumdkit'), *args], cwd=cwd, env=env,
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    (output / (name + '.log')).write_text(result.stdout)
    if result.returncode:
        raise RuntimeError(name + ': ' + result.stdout[-5000:])
    return result.stdout


conversion = output / 'conversion'; conversion.mkdir()
atoms = Atoms('Si2', positions=[[0.1, 0.2, 0.3], [4.9, 0.2, 0.3]], cell=[5, 5, 5], pbc=True)
write(conversion / 'POSCAR', atoms, format='vasp', direct=True)
command('poscar-to-xyz', ['-pos2exyz', 'POSCAR', 'converted.xyz'], conversion)
observed = read(conversion / 'converted.xyz')
assert observed.get_chemical_symbols() == ['Si', 'Si']
np.testing.assert_allclose(observed.positions, atoms.positions, atol=1e-12)
np.testing.assert_allclose(observed.cell, atoms.cell, atol=1e-12)
checks['poscar_conversion'] = True
distance = command('periodic-distance', ['-min_dist_pbc', 'converted.xyz'], conversion)
match = re.search(r'Overall min_distance:\s*([0-9.eE+-]+)', distance)
assert match and abs(float(match[1]) - 0.2) < 1e-10, distance
checks['periodic_distance_angstrom'] = 0.2
write(conversion / 'structure.data', atoms, format='lammps-data', atom_style='atomic')
with gzip.open(conversion / 'structure.data.gz', 'wb') as compressed:
    compressed.write((conversion / 'structure.data').read_bytes())
command('compressed-lammps-data', ['-lmp2exyz', 'structure.data.gz', 'Si'], conversion)
observed = read(conversion / 'dump.xyz')
assert observed.get_chemical_symbols() == ['Si', 'Si']
np.testing.assert_allclose(observed.positions, atoms.positions, atol=1e-10)
checks['compressed_lammps_data_conversion'] = True

prediction = output / 'prediction'; prediction.mkdir()
for name, sha in {
    'nep.txt': 'c772e70b32e5a085936f04e5f5382c7af88a03cb68765510ac82f71ae97eab2c',
    'train.xyz': '9679ecdac1ea13300f9bf208b7d4393e5dfd0e0527a46653de38e4c80bff74ab',
}.items():
    assert hashlib.sha256((fixtures / name).read_bytes()).hexdigest() == sha
    shutil.copyfile(fixtures / name, prediction / name)
# Multiple equivalent frames exercise the multiworker path and plotting arrays.
frame = (prediction / 'train.xyz').read_text()
(prediction / 'train.xyz').write_text(frame * 3)
command('nep-prediction', ['-prediction', 'train.xyz', 'nep.txt', '2'], prediction)
energy = np.atleast_2d(np.loadtxt(prediction / 'energy_train.out'))
force = np.atleast_2d(np.loadtxt(prediction / 'force_train.out'))
virial = np.atleast_2d(np.loadtxt(prediction / 'virial_train.out'))
for data in (energy, force, virial):
    assert np.isfinite(data).all()
assert energy.shape == (3, 2), energy.shape
np.testing.assert_allclose(energy[:, 0], energy[:, 1], atol=1e-6, rtol=0)
np.testing.assert_allclose(force[:, :3], force[:, 3:], atol=1e-6, rtol=0)
np.testing.assert_allclose(virial[:, :6], virial[:, 6:], atol=5e-6, rtol=0)
checks['nep_prediction'] = dict(frames=3, workers=2,
    energy_max_error=float(np.max(np.abs(energy[:, 0] - energy[:, 1]))),
    force_max_error=float(np.max(np.abs(force[:, :3] - force[:, 3:]))),
    virial_max_error=float(np.max(np.abs(virial[:, :6] - virial[:, 6:]))))
command('prediction-plot', ['-plt', 'prediction', 'save'], prediction)
png = (prediction / 'prediction.png').read_bytes()
assert png.startswith(b'\x89PNG\r\n\x1a\n') and len(png) > 1000
checks['prediction_plot_bytes'] = len(png)

doctor = command('doctor', ['-doctor'], output)
for package in ('numpy', 'scipy', 'matplotlib', 'ase', 'pandas', 'pymatgen', 'calorine', 'NepTrain'):
    assert any('[OK]' in line and package in line for line in doctor.splitlines()), package
checks['dependencies'] = True
report = dict(passed=True, prefix=str(prefix), python=sys.version, checks=checks,
              route=os.environ.get('SAI_GPUMDKIT_ROUTE'), job=os.environ.get('SLURM_JOB_ID'),
              image_sha256=os.environ.get('SAI_GPUMDKIT_IMAGE_SHA256'),
              files={str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in output.rglob('*') if p.is_file() and not any(x.startswith('.') for x in p.relative_to(output).parts)})
(output / 'acceptance.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report['checks'], indent=2))
print('GPUMDKIT_FUNCTIONAL_ACCEPTANCE_PASSED')
