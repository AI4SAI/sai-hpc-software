#!/usr/bin/env python3
"""Validate final-prefix dependency evidence and emit CP2K build variables.

No fallback to another CP2K toolchain, no directory-name guessing, and no
renaming of installed Spack stores. The build must consume its own receipt.
"""
import argparse
import json
from pathlib import Path, PurePosixPath
import shlex

from cp2k_spack import validated_prefix
from cp2k_spack_environment import ROOT_SPECS

ROOTS = {s.split('@')[0] for s in ROOT_SPECS} | {'cosma', 'spla'}


def validate_receipt(data, partition, prefix):
    prefix = validated_prefix(partition, prefix, app_only=True)
    if (data.get('partition') != partition or data.get('install_prefix') != prefix or
            data.get('dependencies_installed') is not True or
            data.get('cp2k_built') is not False or data.get('published') is not False):
        raise ValueError('dependency receipt differs from this final-prefix build')
    roots = data.get('roots', {})
    runtime = data.get('runtime_prefixes', [])
    if set(roots) != ROOTS or not runtime or len(runtime) != len(set(runtime)):
        raise ValueError('incomplete or duplicate dependency closure')
    store = PurePosixPath(prefix) / 'dependencies/spack'
    for path in [*roots.values(), *runtime]:
        p = PurePosixPath(path)
        if (str(p) != path or '..' in p.parts or not p.is_relative_to(store) or
                len(p.relative_to(store).parts) != 2):
            raise ValueError('dependency path escaped the exact final store')
    nodes = data.get('nodes', [])
    installed = {n['prefix']: n for n in nodes if n.get('external') is False}
    if (len(installed) != sum(n.get('external') is False for n in nodes) or
            not set(runtime) <= installed.keys() or not set(roots.values()) <= set(runtime)):
        raise ValueError('dependency closure not proven installed')
    if any(installed[path]['name'] != name for name, path in roots.items()):
        raise ValueError('dependency root identity mismatch')
    return roots, runtime


def check_files(roots):
    # The kernel is a distinct library, not a versioned alias of libplumed.so.
    for name in ('libplumed.so', 'libplumedKernel.so'):
        root = Path(roots['plumed'])
        matches = [p for d in ('lib', 'lib64') for p in (root / d).glob(name + '*')
                   if p.is_file() and p.resolve().is_relative_to(root)]
        if not matches:
            raise ValueError('missing private PLUMED library: ' + name)
    for value in roots.values():
        path = Path(value)
        if not path.is_dir() or path.resolve() != path:
            raise ValueError('missing or redirected dependency prefix: ' + value)


def shell_variables(data, partition, prefix):
    roots, runtime = validate_receipt(data, partition, prefix)
    lines = ['cp2k_spack_prefixes=(' + shlex.join([roots[n] for n in sorted(roots)]) + ')']
    for name in ('libint', 'libxsmm', 'hdf5', 'plumed'):
        lines.append('export CP2K_' + name.upper() + '_ROOT=' + shlex.quote(roots[name]))
    directories = [p + '/' + lib for p in runtime for lib in ('lib', 'lib64')]
    lines.append('cp2k_spack_runtime_ld=' + shlex.quote(':'.join(directories)))
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('receipt', type=Path)
    parser.add_argument('partition')
    parser.add_argument('prefix')
    args = parser.parse_args()
    data = json.loads(args.receipt.read_text())
    roots, _ = validate_receipt(data, args.partition, args.prefix)
    check_files(roots)
    print(shell_variables(data, args.partition, args.prefix), end='')


if __name__ == '__main__':
    main()
