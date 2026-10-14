#!/usr/bin/env python3
"""Record the native dependency pilot, never label it a CP2K acceptance."""
import json
from pathlib import Path
import sys
from cp2k_spack import validated_prefix


def collect(partition, install_prefix=None):
    import spack.environment as ev

    env = ev.active_environment()
    if env is None:
        raise ValueError('no active environment')
    install_prefix = validated_prefix(partition, install_prefix)
    prefix = Path(install_prefix) / 'dependencies/spack'
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
    roots = {s.name: str(s.prefix) for s in env.concrete_roots()}
    runtime = {str(s.prefix) for root in env.concrete_roots()
               for s in root.traverse(deptype=('link', 'run')) if not s.external}
    with Path('/results/install-success.json').open('x') as stream:
        json.dump({'partition': partition, 'dependencies_installed': True,
                   'install_prefix': install_prefix,
                   'cp2k_built': False, 'scientific_verified': False, 'published': False,
                   'roots': roots, 'runtime_prefixes': sorted(runtime),
                   'nodes': records}, stream, indent=2, sort_keys=True)
        stream.write('\n')


if __name__ == '__main__':
    collect(*sys.argv[1:])
