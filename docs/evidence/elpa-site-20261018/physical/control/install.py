#!/usr/bin/env python3
"""Admin handoff: verify/copy only the two inventoried ELPA installation trees."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys

PACKAGE = Path(__file__).resolve().parent
NAME = '2026.02.001-2603-gnu-auto'
MODULE = Path('/opt/modules/modulefiles/devtools/elpa') / NAME


def checksum(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def require(value, message):
    if not value:
        raise ValueError(message)


def plain(path):
    require(path.is_absolute() and '..' not in path.parts, f'Invalid path: {path}')
    for parent in [*reversed(path.parents), path]:
        require(not parent.is_symlink(), f'Symlink in directory path: {parent}')


def inventory(root):
    plain(root)
    require(root.is_dir(), f'Missing directory: {root}')
    result = []
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(parent) / name
            info = path.lstat()
            row = dict(path=str(path.relative_to(root)), mode=stat.S_IMODE(info.st_mode))
            require(not row['mode'] & 0o7022 or path.is_symlink(), f'Unsafe permissions: {path}')
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                require(not target.startswith('/') and path.resolve(strict=True).is_relative_to(root),
                        f'External or dangling symlink: {path}')
                row.update(kind='symlink', target=target)
            elif stat.S_ISDIR(info.st_mode):
                row.update(kind='directory')
            elif stat.S_ISREG(info.st_mode):
                row.update(kind='file', size=info.st_size, sha256=checksum(path))
            else:
                raise ValueError(f'Special file in ELPA payload: {path}')
            result.append(row)
    return sorted(result, key=lambda row: row['path'])


def verify(root, expected):
    require(inventory(root) == expected, f'Payload differs from manifest: {root}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='Read-only source and target preflight (default)')
    mode.add_argument('--install', action='store_true', help='Install the two new prefixes and new auto module')
    mode.add_argument('--verify', action='store_true', help='Verify installed physical files')
    args = parser.parse_args()
    release = json.loads((PACKAGE/'release.json').read_text())
    require(set(release['builds']) == {'avx2', 'avx512'}, 'Expected both ISA builds')
    require(checksum(PACKAGE/NAME) == release['module_sha256'], 'Module checksum mismatch')
    plain(MODULE)
    if MODULE.exists():
        require(checksum(MODULE) == release['module_sha256'], f'Refusing to replace existing module: {MODULE}')
    for isa, build in release['builds'].items():
        prefix = Path(f'/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-{isa}/nvidia')
        require(build['prefix'] == str(prefix), 'Unexpected installation prefix')
        manifest_path = PACKAGE/f'{isa}-manifest.json'
        require(checksum(manifest_path) == build['manifest_sha256'], 'Manifest checksum mismatch')
        manifest = json.loads(manifest_path.read_text())
        for row in manifest:
            name = PurePosixPath(row['path'])
            require(not name.is_absolute() and '..' not in name.parts, 'Unsafe manifest member')
        plain(prefix)
        if args.verify:
            verify(prefix, manifest)
            print(f'{isa}: physical installation verified', flush=True)
            continue
        source = PACKAGE/'payload'/isa
        verify(source, manifest)
        if prefix.exists():
            verify(prefix, manifest)
        else:
            require(not prefix.parent.exists(), f'Installation parent already exists: {prefix.parent}')
        size = sum(row.get('size', 0) for row in manifest)
        print(f'{isa}: {size / 1024**2:.2f} MiB verified; {prefix}', flush=True)
    if args.verify:
        require(MODULE.is_file(), 'Physical module is missing')
        print('Physical ELPA files and module verified. Run numerical checks inside a compute allocation.')
        return
    if not args.install:
        print('Preflight passed. Administrator: bash install.sh --install')
        return
    # No container execution and no build/cache/source-tree copying as admin.
    for isa, build in release['builds'].items():
        prefix = Path(build['prefix'])
        if prefix.exists():
            continue
        stage = prefix.parent.with_name('.'+prefix.parent.name+f'.install-{os.getpid()}')
        stage.mkdir(mode=0o755)
        shutil.copytree(PACKAGE/'payload'/isa, stage/'nvidia', symlinks=True)
        (stage/'nvidia').chmod(0o755)
        expected = json.loads((PACKAGE/f'{isa}-manifest.json').read_text())
        verify(stage/'nvidia', expected)
        require(not prefix.parent.exists(), f'Target appeared during copy: {prefix.parent}')
        stage.rename(prefix.parent)
    if not MODULE.exists():
        with MODULE.open('xb') as stream:
            stream.write((PACKAGE/NAME).read_bytes())
        MODULE.chmod(0o644)
    print('Installed both ELPA variants and '+str(MODULE))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        sys.exit('Deployment stopped: '+str(exc))
