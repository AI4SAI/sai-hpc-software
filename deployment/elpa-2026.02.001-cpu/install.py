#!/usr/bin/env python3
"""Add the accepted CPU prefix and partition module; verify existing GPU prerequisites."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys

PACKAGE = Path(__file__).resolve().parent
NAME = '2026.02.001-2603-gnu-partition'
PREFIX = Path('/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx512/cpu')
MODULE = Path('/opt/modules/modulefiles/devtools/elpa') / NAME


def require(ok, message):
    if not ok:
        raise ValueError(message)


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


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
            path = Path(parent)/name
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
                raise ValueError(f'Special file: {path}')
            result.append(row)
    return sorted(result, key=lambda row: row['path'])


def manifest(name, digest):
    path = PACKAGE/name
    require(checksum(path) == digest, f'Manifest checksum mismatch: {name}')
    rows = json.loads(path.read_text())
    for row in rows:
        member = PurePosixPath(row['path'])
        require(not member.is_absolute() and '..' not in member.parts, 'Unsafe inventory member')
    return rows


def verify(root, rows):
    require(inventory(root) == rows, f'Installed content differs: {root}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--check', action='store_true')
    modes.add_argument('--install', action='store_true')
    modes.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    release = json.loads((PACKAGE/'release.json').read_text())
    require(release['prefix'] == str(PREFIX), 'Unexpected CPU prefix')
    require(checksum(PACKAGE/NAME) == release['module_sha256'], 'Module checksum mismatch')
    plain(PREFIX)
    plain(MODULE)
    if MODULE.exists():
        require(checksum(MODULE) == release['module_sha256'], 'Existing partition module differs')
    rows = manifest('cpu-manifest.json', release['manifest_sha256'])
    require(set(release['gpu_prerequisites']) == {'avx2', 'avx512'}, 'Missing GPU prerequisites')
    for isa, build in release['gpu_prerequisites'].items():
        prefix = Path(f'/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-{isa}/nvidia')
        require(build['prefix'] == str(prefix), 'Unexpected GPU prerequisite prefix')
        verify(prefix, manifest(f'{isa}-manifest.json', build['manifest_sha256']))
        print(f'{isa}: existing GPU prerequisite verified', flush=True)
    if args.verify:
        verify(PREFIX, rows)
        require(MODULE.is_file(), 'Partition module is missing')
        print('Physical CPU ELPA and partition module verified; compute-node runtime check is separate.')
        return
    verify(PACKAGE/'payload/cpu', rows)
    if PREFIX.exists():
        verify(PREFIX, rows)
    print(f'CPU payload: {sum(row.get("size", 0) for row in rows)/1024**2:.2f} MiB verified', flush=True)
    if not args.install:
        print('Preflight passed. Administrator: bash install.sh --install')
        return
    if not PREFIX.exists():
        stage = PREFIX.with_name(f'.cpu.install-{os.getpid()}')
        shutil.copytree(PACKAGE/'payload/cpu', stage, symlinks=True)
        stage.chmod(0o755)
        verify(stage, rows)
        require(not PREFIX.exists(), 'CPU destination appeared during copy')
        stage.rename(PREFIX)
    if not MODULE.exists():
        with MODULE.open('xb') as stream:
            stream.write((PACKAGE/NAME).read_bytes())
        MODULE.chmod(0o644)
    print(f'Installed {PREFIX} and {MODULE}')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        sys.exit('Deployment stopped: '+str(exc))
