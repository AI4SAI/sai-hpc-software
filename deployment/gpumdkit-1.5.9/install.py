#!/usr/bin/env python3
"""Export the accepted GPUMDkit SIF to its compiled prefix and publish its module."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from posixpath import normpath

PACKAGE = Path(__file__).resolve().parent
PREFIX = Path('/opt/apps/GPUMDkit/1.5.9')
MODULE = Path('/opt/modules/modulefiles/apps/gpumdkit/1.5.9')


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def plain(path):
    require(path.is_absolute() and '..' not in path.parts, 'noncanonical path')
    require(not any(p.is_symlink() for p in [path, *path.parents]), 'symlink in installation path')


def directories(path):
    plain(path)
    for parent in [*reversed(path.parents), path]:
        if not parent.exists():
            parent.mkdir(mode=0o755)
            parent.chmod(0o755)
        require(parent.is_dir(), 'non-directory parent')


def validate_inventory(records):
    expected = {r['path']: r for r in records}
    require(len(expected) == len(records) and expected.get('.', {}).get('kind') == 'directory',
            'duplicate or missing inventory root')
    for row in records:
        name = Path(row['path'])
        require(not name.is_absolute() and '..' not in name.parts and str(name) == row['path']
                and row['kind'] in ('file', 'directory', 'symlink')
                and type(row['mode']) is int and 0 <= row['mode'] <= 0o777, 'invalid inventory entry')
        for parent in name.parents:
            require(expected.get(str(parent), {}).get('kind') == 'directory', 'missing parent directory')
        if row['kind'] != 'symlink':
            continue
        current, visited = row['path'], set()
        while expected.get(current, {}).get('kind') == 'symlink':
            require(current not in visited, 'cyclic payload link')
            visited.add(current)
            target = expected[current]['target']
            require(not Path(target).is_absolute(), 'absolute runtime link needs review')
            current = normpath(str(Path(current).parent / target))
            require(current != '..' and not current.startswith('../'), 'link escapes prefix')
        require(current in expected, 'dangling payload link')


def verify(root, records):
    plain(root)
    expected = {r['path']: r for r in records}
    seen = set()
    for path in [root, *root.rglob('*')]:
        name = str(path.relative_to(root)); row = expected.get(name)
        require(row is not None, 'unlisted file: ' + name)
        seen.add(name)
        info = path.lstat()
        if row['kind'] == 'symlink':
            require(path.is_symlink() and os.readlink(path) == row['target'], 'changed symlink: ' + name)
        else:
            require(not path.is_symlink() and stat.S_IMODE(info.st_mode) == row['mode'], 'changed mode: ' + name)
            if row['kind'] == 'directory':
                require(path.is_dir(), 'missing directory')
            else:
                require(path.is_file() and info.st_size == row['size'] and digest(path) == row['sha256'],
                        'changed payload: ' + name)
    require(seen == set(expected), 'missing installed files')


def module_text(root=Path('/')):
    text = (PACKAGE / 'modulefile').read_text()
    return text if root == Path('/') else text.replace(str(PREFIX), str(root / PREFIX.relative_to('/')))


def preflight(installed=False, root=Path('/')):
    release = json.loads((PACKAGE / 'release.json').read_text())
    require(release['version'] == '1.5.9' and release['source_sha'] == '073371a45c9f58b0c0e2ca9ba7a7934705c6a677'
            and release['prefix'] == str(PREFIX), 'wrong release')
    for name, sha in release['metadata'].items():
        require(not Path(name).is_absolute() and '..' not in Path(name).parts, 'invalid metadata path')
        require(digest(PACKAGE / name) == sha, 'changed metadata: ' + name)
    records = json.loads((PACKAGE / 'inventory.json').read_text())
    validate_inventory(records)
    proof = json.loads((PACKAGE / 'acceptance.json').read_text())
    require(proof['passed'] is True and proof['route'] == 'sif' and proof['prefix'] == str(PREFIX)
            and proof['image_sha256'] == release['image_sha256']
            and proof['job'] == release['accounting'][0] and release['accounting'][1:3] == ['COMPLETED', '0:0'],
            'SIF not accepted')
    image = PACKAGE / 'gpumdkit-1.5.9.sif'
    if not installed:
        require(image.is_file() and image.stat().st_size == release['image_bytes']
                and digest(image) == release['image_sha256'], 'SIF changed')
    target, module = root / PREFIX.relative_to('/'), root / MODULE.relative_to('/')
    plain(target); plain(module)
    if installed or target.exists():
        verify(target, records)
    if installed or module.exists():
        require(module.is_file() and stat.S_IMODE(module.stat().st_mode) == 0o644
                and module.read_text() == module_text(root), 'module differs')
    print('GPUMDKIT_' + ('INSTALLED_VERIFIED' if installed else 'PREFLIGHT_PASSED'))
    return release, records


def install(root=Path('/')):
    release, records = preflight(root=root)
    target, module = root / PREFIX.relative_to('/'), root / MODULE.relative_to('/')
    directories(target.parent)
    descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if not target.exists():
            stage = target.with_name('.1.5.9-incoming-' + str(os.getpid()))
            require(not stage.exists(), 'staging destination exists')
            image = PACKAGE / 'gpumdkit-1.5.9.sif'
            listing = subprocess.check_output(['/opt/devtools/apptainer/1.4.4/bin/apptainer',
                                                'sif', 'list', str(image)], text=True)
            offset, = re.findall(r'\|\s*(\d+)-\d+\s*\|\s*FS \(Squashfs/\*System/', listing)
            subprocess.run(['unsquashfs', '-processors', '2', '-no-xattrs', '-no-progress', '-o', offset,
                            '-d', str(stage), str(image), str(PREFIX).lstrip('/')], check=True)
            payload = stage / PREFIX.relative_to('/')
            verify(payload, records)
            require(not target.exists() and not target.is_symlink(), 'destination appeared')
            payload.rename(target)
            # Only remove the extraction directories created by this install.
            current = payload.parent
            while current != stage.parent:
                parent = current.parent; current.rmdir(); current = parent
        verify(target, records)
        directories(module.parent)
        if not module.exists():
            stage = module.with_name('.1.5.9-incoming-' + str(os.getpid()))
            with stage.open('xb') as stream:
                stream.write(module_text(root).encode()); stream.flush(); os.fsync(stream.fileno())
            stage.chmod(0o644); os.link(stage, module); stage.unlink()
        preflight(installed=True, root=root)
    finally:
        os.close(descriptor)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--check', action='store_true')
    action.add_argument('--install', action='store_true')
    action.add_argument('--verify', action='store_true')
    parser.add_argument('--stage-root', type=Path, default=Path('/'))
    args = parser.parse_args()
    try:
        root = args.stage_root
        plain(root)
        require(root == Path('/') or root.is_relative_to('/org/org-open-sai/proj-sai-hpc-software/operations'),
                'stage root must be below project operations')
        install(root) if args.install else preflight(args.verify, root)
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit('Installation stopped: ' + str(error)) from error
