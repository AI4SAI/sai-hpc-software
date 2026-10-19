#!/usr/bin/env python3
"""Replace the three known ELPA installations; retain their complete previous trees."""
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


PREFIXES = {
    'avx2': Path('/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx2/nvidia'),
    'avx512': Path('/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx512/nvidia'),
    'dsprhbm': Path('/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx512/cpu'),
}


def read_manifest(name, digest):
    path = PACKAGE/name
    require(checksum(path) == digest, f'Manifest checksum mismatch: {name}')
    rows = json.loads(path.read_text())
    for row in rows:
        member = PurePosixPath(row['path'])
        require(not member.is_absolute() and '..' not in member.parts, 'Unsafe manifest member')
    return rows


def installation_state(prefix, before, after):
    actual = inventory(prefix)
    if actual == after:
        return 'repaired'
    require(actual == before, f'Existing installation differs from the accepted old and repaired versions: {prefix}')
    return 'previous'


def replace_one(prefix, source, before, after, old_digest):
    if installation_state(prefix, before, after) == 'repaired':
        return
    backup = prefix.with_name('.'+prefix.name+'.before-'+old_digest[:16])
    stage = prefix.with_name('.'+prefix.name+f'.commfix-install-{os.getpid()}')
    plain(backup)
    plain(stage)
    require(not backup.exists(), f'Backup already exists; inspect it before another replacement: {backup}')
    require(not stage.exists(), f'Staging directory already exists: {stage}')
    shutil.copytree(source, stage, symlinks=True)
    stage.chmod(0o755)
    verify(stage, after)
    # Recheck immediately before switching. Never truncate the old .so: running
    # processes retain its inode, and the complete old prefix stays recoverable.
    verify(prefix, before)
    require(not backup.exists(), f'Backup appeared during copy: {backup}')
    prefix.rename(backup)
    try:
        stage.rename(prefix)
    except OSError:
        backup.rename(prefix)
        raise
    print(f'Previous installation preserved: {backup}', flush=True)
    verify(prefix, after)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--check', action='store_true', help='Read-only payload and existing-installation check (default)')
    modes.add_argument('--install', action='store_true', help='Back up and replace the three known installation trees')
    modes.add_argument('--verify', action='store_true', help='Verify the repaired physical files and existing auto module')
    args = parser.parse_args()
    release = json.loads((PACKAGE/'release.json').read_text())
    require(release['revision'] == 'commfix-1' and set(release['builds']) == set(PREFIXES), 'Unexpected repair release')
    require(release['upstream_commit'] == '20be982db53a671b9aac866a726aebe153a766e6', 'Unexpected repair commit')
    plain(MODULE)
    require(checksum(PACKAGE/NAME) == release['module_sha256'] == checksum(MODULE),
            'Installed auto module differs from the accepted partition selector')
    plan = []
    for key, prefix in PREFIXES.items():
        build = release['builds'][key]
        require(build['prefix'] == str(prefix), 'Unexpected installation prefix')
        before = read_manifest(f'{key}-previous-manifest.json', build['previous_manifest_sha256'])
        after = read_manifest(f'{key}-manifest.json', build['manifest_sha256'])
        state = installation_state(prefix, before, after)
        if args.verify:
            require(state == 'repaired', f'Repair has not been installed: {prefix}')
            print(f'{key}: repaired physical files verified', flush=True)
            continue
        source = PACKAGE/'payload'/key
        verify(source, after)
        fix = json.loads((source/'share/sai/mpi-communicator-fix.json').read_text())
        require(fix['upstream_commit'] == release['upstream_commit'] and
                fix['patch_sha256'] == release['communicator_patch_sha256'], 'Missing reviewed communicator repair')
        backup = prefix.with_name('.'+prefix.name+'.before-'+build['previous_manifest_sha256'][:16])
        plain(backup)
        if state == 'previous':
            require(not backup.exists(), f'Backup destination already exists: {backup}')
        elif backup.exists():
            verify(backup, before)
        size = sum(row.get('size', 0) for row in after)
        print(f'{key}: {size/1024**2:.2f} MiB; current={state}; {prefix}', flush=True)
        plan.append((prefix, source, before, after, build['previous_manifest_sha256']))
    if args.verify:
        print('Repaired physical ELPA files and existing auto module verified. Compute-node runtime acceptance is separate.')
    elif args.install:
        for entry in plan:
            replace_one(*entry)
        print('Installed all three repaired ELPA variants. The existing auto module selects the same paths.')
    else:
        print('Preflight passed. Administrator: bash install.sh --install')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        sys.exit('Deployment stopped: '+str(exc))

