#!/usr/bin/env python3
"""Record the native dependency pilot, never label it a CP2K acceptance."""
import json
from pathlib import Path
import sys


def collect(partition):
    import spack.environment as ev

    env = ev.active_environment()
    if env is None:
        raise ValueError('no active environment')
    prefix = Path('/opt/software/cp2k/development/spack-native-probe') / partition / 'dependencies/spack'
    records = []
    for spec in env.all_specs():
        if not spec.installed:
            raise ValueError('dependency installation incomplete: ' + str(spec))
        path = Path(str(spec.prefix))
        if not spec.external and not path.is_relative_to(prefix):
            raise ValueError('dependency escaped its canonical pilot store')
        records.append({'name': spec.name, 'hash': spec.dag_hash(), 'prefix': str(path),
                        'external': bool(spec.external), 'spec': str(spec)})
    plumed = next(s for s in env.concrete_roots() if s.name == 'plumed')
    plumed_root = Path(str(plumed.prefix))
    if not any(plumed_root.glob('lib*/libplumedKernel.so*')):
        raise ValueError('independent PLUMED kernel library is missing')
    with Path('/results/install-success.json').open('x') as stream:
        json.dump({'partition': partition, 'dependencies_installed': True,
                   'cp2k_built': False, 'scientific_verified': False, 'published': False,
                   'nodes': records}, stream, indent=2, sort_keys=True)
        stream.write('\n')


if __name__ == '__main__':
    collect(sys.argv[1])
