#!/usr/bin/env python3
"""Record and check the public module in an actual Slurm GPU allocation."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

stage, output = sys.argv[1], Path(sys.argv[2])
package = Path('/home/stardust/sai-hpc-software/deployment/nepadapters-1.0.4-shared')
release = json.loads((package / 'release.json').read_text())
prefix = release['shared']['prefix']
part = os.environ['SLURM_JOB_PARTITION']
expected = release['partitions'][part]['lammps']
module = 'nepadapters/1.0.4-lammps30Sep2026'
lammps = 'lammps/30Sep2026-deepmd320-auto'
keys = ('PATH', 'LD_LIBRARY_PATH', 'LAMMPS_PLUGIN_PATH', 'SAI_NEPADAPTERS_PREFIX',
        'SAI_LAMMPS_PREFIX', 'LOADEDMODULES')
environment = {key: os.environ.get(key, '') for key in keys}
loaded = environment['LOADEDMODULES'].split(':')
plugins = environment['LAMMPS_PLUGIN_PATH'].split(':')
sha = hashlib.sha256(Path(release['module']).read_bytes()).hexdigest()
assert sha == release['module_sha256']
assert 'SAI_NEPADAPTERS_PARTITION' not in os.environ

if stage in ('auto', 'attached'):
    assert module in loaded and lammps in loaded
    assert environment['SAI_NEPADAPTERS_PREFIX'] == prefix
    assert plugins.count(prefix + '/lib') == 1
else:
    assert module not in loaded
    assert not environment['SAI_NEPADAPTERS_PREFIX']
    assert prefix + '/lib' not in plugins

if stage in ('auto', 'baseline', 'attached', 'detached'):
    assert lammps in loaded
    assert environment['SAI_LAMMPS_PREFIX'] == expected['prefix']
    assert shutil.which('lmp') == expected['prefix'] + '/bin/lmp'
    isa = expected['identity']['dependency_isa']
    assert shutil.which('mpirun') == '/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-' + isa + '/bin/mpirun'
else:
    assert lammps not in loaded
    assert not environment['SAI_LAMMPS_PREFIX']

if stage in ('attached', 'detached'):
    baseline = json.loads((output / 'baseline.json').read_text())['environment']
    assert all(environment[key] == baseline[key] for key in ('PATH', 'LD_LIBRARY_PATH', 'SAI_LAMMPS_PREFIX'))
    expected_plugins = ([prefix + '/lib'] if stage == 'attached' else []) + [p for p in baseline['LAMMPS_PLUGIN_PATH'].split(':') if p]
    assert [p for p in plugins if p] == expected_plugins

if stage == 'auto':
    styles = (output / 'lmp-styles.txt').read_text()
    assert 'nep/cpu' in styles and 'nep/gpu/kk' in styles

record = dict(passed=True, stage=stage, job=os.environ['SLURM_JOB_ID'], partition=part,
              module_sha256=sha, environment=environment)
(output / (stage + '.json')).write_text(json.dumps(record, indent=2) + '\n')
print('NEPADAPTERS_MODULE_' + stage.upper() + '_PASSED')
