#!/usr/bin/env python3
"""Export the accepted standard NepTrainKit runtime; public installation is manual."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess

from prepare import APP, PREFIX, SOURCE, digest, offset

PACKAGE = Path(__file__).resolve().parent
MODULE = Path('/opt/modules/modulefiles/apps/neptrainkit/3.1.5')
IMAGE_SHA256 = '59549f1d89878a1f262f7090b60a5a5e73fff7b2c7e2b523a5e1e2e75e72903b'


def require(value, message):
    if not value:
        raise ValueError(message)


def plain(path):
    require(path.is_absolute() and '..' not in path.parts, 'noncanonical path')
    for p in [*reversed(path.parents), path]:
        require(not p.is_symlink(), 'symlink ancestor: ' + str(p))


def directory(path):
    plain(path)
    for p in [*reversed(path.parents), path]:
        if not p.exists():
            p.mkdir(mode=0o755)
            p.chmod(0o755)
        require(p.is_dir(), 'non-directory ancestor: ' + str(p))


def omitted(name):
    parts = Path(name).parts
    if '__pycache__' in parts or Path(name).suffix in ('.pyc', '.pyo'):
        return True
    if parts[:3] == ('lib', 'python3.12', 'test'):
        return True
    if len(parts) >= 5 and parts[:3] == ('lib', 'python3.12', 'site-packages'):
        if parts[3] in ('numpy', 'scipy', 'matplotlib', 'ase') and any(p in ('test', 'tests') for p in parts[4:]):
            return True
    return False


def inventory(root, projected=False):
    plain(root)
    records, pending, size = [], [root], 0
    while pending:
        path = pending.pop()
        relative = str(path.relative_to(root))
        if omitted(relative):
            require(projected, 'omitted test/cache remains: ' + relative)
            continue
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            target = os.readlink(path)
            require(not target.startswith('/') or target.startswith((PREFIX + '/', '/usr/', '/lib/', '/lib64/', '/opt/devtools/')),
                    'unexpected absolute link: ' + relative)
            records.append(dict(path=relative, kind='link', target=target))
        elif stat.S_ISDIR(info.st_mode):
            if not projected:
                require(stat.S_IMODE(info.st_mode) == 0o755, 'nonpublic directory: ' + relative)
            records.append(dict(path=relative, kind='directory', mode=0o755))
            pending.extend(path.iterdir())
        else:
            require(stat.S_ISREG(info.st_mode), 'special payload entry')
            mode = 0o755 if info.st_mode & 0o111 else 0o644
            if not projected:
                require(stat.S_IMODE(info.st_mode) == mode, 'noncanonical file mode: ' + relative)
            size += info.st_size
            records.append(dict(path=relative, kind='file', mode=mode, size=info.st_size, sha256=digest(path)))
    records.sort(key=lambda row: row['path'])
    # Retained links must resolve inside the projected runtime or declared host roots.
    names = {r['path'] for r in records}
    for row in records:
        if row['kind'] != 'link':
            continue
        target = row['target']
        if target.startswith(PREFIX + '/'):
            resolved = os.path.normpath(target[len(PREFIX)+1:])
        elif target.startswith('/'):
            continue
        else:
            resolved = os.path.normpath(str(Path(row['path']).parent / target))
        require(resolved in names, 'retained link targets missing/omitted file: ' + row['path'])
    encoded = json.dumps(records, sort_keys=True, separators=(',', ':')).encode()
    return dict(sha256=hashlib.sha256(encoded).hexdigest(), entries=len(records),
                files=sum(r['kind'] == 'file' for r in records), bytes=size)


def normalize(root):
    for parent, dirs, files in os.walk(root, topdown=True, followlinks=False):
        path = Path(parent)
        for name in list(dirs):
            child = path / name
            if omitted(str(child.relative_to(root))):
                if child.is_symlink():
                    child.unlink()
                else:
                    shutil.rmtree(child)
                dirs.remove(name)
        for name in files:
            child = path / name
            if omitted(str(child.relative_to(root))):
                child.unlink()
            elif not child.is_symlink():
                child.chmod(0o755 if child.stat().st_mode & 0o111 else 0o644)
        path.chmod(0o755)


def metadata():
    release = json.loads((PACKAGE / 'release.json').read_text())
    require(release['schema'] == 1 and release['source'] == SOURCE and release['version'] == '3.1.5'
            and release['prefix'] == PREFIX and release['image_sha256'] == IMAGE_SHA256, 'wrong release identity')
    for name, expected in release['records'].items():
        path = PACKAGE / name
        require(not Path(name).is_absolute() and '..' not in Path(name).parts, 'unsafe record')
        plain(path)
        require(digest(path) == expected and path.stat().st_mode & 0o004, 'changed/unreadable record: ' + name)
    proof = json.loads((PACKAGE / 'acceptance.json').read_text())
    require(proof['passed'] is True and proof['image_sha256'] == IMAGE_SHA256
            and proof['source'] == SOURCE and proof['version'] == '3.1.5', 'science proof/image mismatch')
    return release


def module_text(root):
    return (PACKAGE / '3.1.5').read_text().replace(PREFIX, str(root / PREFIX.lstrip('/')))


def check(release, root, installed=False):
    image = PACKAGE / 'neptrainkit-3.1.5.sif'
    plain(image)
    require(digest(image) == IMAGE_SHA256, 'changed SIF')
    target, module = root / PREFIX.lstrip('/'), root / MODULE.relative_to('/')
    plain(target); plain(module)
    if installed or target.exists():
        require(inventory(target) == release['runtime'], 'installed runtime differs')
    if installed or module.exists():
        require(module.is_file() and stat.S_IMODE(module.stat().st_mode) == 0o644
                and module.read_text() == module_text(root), 'installed module differs')


def install(release, root):
    check(release, root)
    target, module = root / PREFIX.lstrip('/'), root / MODULE.relative_to('/')
    directory(target.parent)
    descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if not target.exists():
            stage = target.with_name('.' + target.name + '.incoming-' + str(os.getpid()))
            require(not stage.exists() and not stage.is_symlink(), 'staging path already exists')
            directory(stage)
            image = PACKAGE / 'neptrainkit-3.1.5.sif'
            subprocess.run(['unsquashfs', '-processors', '2', '-no-progress', '-no-xattrs',
                '-o', str(offset(image)), '-d', str(stage), str(image), PREFIX.lstrip('/')], check=True)
            payload = stage / PREFIX.lstrip('/')
            normalize(payload)
            require(inventory(payload) == release['runtime'], 'extracted runtime differs')
            require(not target.exists() and not target.is_symlink(), 'destination appeared during installation')
            payload.rename(target)
            # Remove only the now-empty parents created for this extraction.
            for p in (stage / PREFIX.lstrip('/')).parents:
                p.rmdir()
                if p == stage:
                    break
        directory(module.parent)
        if not module.exists():
            stage = module.with_name('.' + module.name + '.incoming-' + str(os.getpid()))
            with stage.open('x') as stream:
                stream.write(module_text(root)); stream.flush(); os.fsync(stream.fileno())
            stage.chmod(0o644)
            os.link(stage, module); stage.unlink()
        check(release, root, installed=True)
    finally:
        os.close(descriptor)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    for operation in ('check', 'install', 'verify'):
        group.add_argument('--' + operation, action='store_true')
    parser.add_argument('--stage-root', type=Path)
    args = parser.parse_args()
    root = args.stage_root or Path('/')
    if args.stage_root:
        require(root.is_relative_to('/org/org-open-sai/proj-sai-hpc-software/operations'), 'stage root must be project operations')
    try:
        release = metadata()
        if args.install:
            install(release, root)
        else:
            check(release, root, installed=args.verify)
        print('NEPTRAINKIT_' + ('INSTALLED' if args.install else 'VERIFIED' if args.verify else 'PREFLIGHT_PASSED'))
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit('Installation stopped: ' + str(error)) from error
