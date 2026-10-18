#!/usr/bin/env python3
"""Stage the CP2K runtime closure at unchanged absolute installation paths."""
import argparse
import json
from pathlib import Path
import shutil

from cp2k_spack_link import validate_receipt


def dependency_ignore(directory, names):
    # These are build/development artifacts, not CP2K or PLUMED runtime data.
    excluded = {'.spack', 'include', 'cmake', 'pkgconfig', 'doc', 'man',
                'test', 'tests', 'examples'}
    return [n for n in names if n in excluded or n.endswith(('.a', '.la'))]


def stage(prefix, root, partition):
    prefix, root = Path(prefix), Path(root)
    if (not root.is_absolute() or root.resolve() != root or root == Path('/') or
            not prefix.is_dir() or prefix.resolve() != prefix or
            root.is_relative_to(prefix) or prefix.is_relative_to(root)):
        raise ValueError('runtime staging requires separate canonical directories')
    data = json.loads((prefix / 'share/sai/spack-dependencies.json').read_text())
    _, runtime = validate_receipt(data, partition, str(prefix))
    destination = root / prefix.relative_to('/')
    if destination.exists() or destination.is_symlink():
        raise ValueError('runtime staging destination already exists')
    destination.mkdir(parents=True)
    for name in ('bin', 'lib', 'lib64', 'share'):
        source = prefix / name
        if source.exists():
            shutil.copytree(source, destination / name, symlinks=True)
    for dependency in ('dbcsr', 'libxs', 'tblite', 'elpa'):
        source = prefix / 'dependencies' / dependency
        if not source.is_dir() or source.is_symlink():
            raise ValueError('missing native dependency: ' + dependency)
        for name in ('lib', 'lib64', 'share'):
            if (source / name).is_dir():
                shutil.copytree(source / name, destination / 'dependencies' / dependency / name,
                                symlinks=True, ignore=dependency_ignore)
    # Build-only Python environments and compiler wrappers are never copied.
    # Spack's recorded link/run closure supplies every private runtime package.
    for value in runtime:
        source = Path(value)
        if not source.is_dir() or source.resolve() != source:
            raise ValueError('missing or redirected runtime package')
        for name in ('lib', 'lib64', 'share'):
            if (source / name).is_dir():
                shutil.copytree(source / name, root / source.relative_to('/') / name,
                                symlinks=True, ignore=dependency_ignore)
    # A separate post-export ELF/inventory check rejects any remaining link
    # to something omitted here. No mutation of the build store occurs.
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('prefix', type=Path)
    parser.add_argument('root', type=Path)
    parser.add_argument('partition')
    args = parser.parse_args()
    print(stage(args.prefix, args.root, args.partition))
