#!/usr/bin/env python3
"""Copy verified dependency archives from a shared cache into new run inputs."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile


def canonical(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path or path.is_symlink():
        raise ValueError('archive path must be absolute and contain no symlinks or traversal')
    return path


def parameters(root, name, digest):
    root = canonical(root)
    if not root.is_dir() or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name):
        raise ValueError('invalid archive root or filename')
    if not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('invalid archive SHA256')
    bucket = canonical(root / 'cache/source-archives' / digest)
    bucket.mkdir(parents=True, exist_ok=True)
    canonical(bucket)
    return root, bucket / name


def input_directory(root, directory):
    directory = canonical(directory)
    try:
        parts = directory.relative_to(root).parts
    except ValueError:
        raise ValueError('archive input escaped project') from None
    if (len(parts) != 4 or parts[0] != 'runs' or parts[2] != 'input' or
            parts[3] not in ('abacus-updates', 'cp2k-updates') or
            not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', parts[1])):
        raise ValueError('archive destination must be a run dependency input')
    directory.mkdir(parents=True, exist_ok=True)
    return canonical(directory)


@contextmanager
def locked(path):
    canonical(path)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'a') as guard:
        if not stat.S_ISREG(os.fstat(guard.fileno()).st_mode):
            raise ValueError('archive lock is not a regular file')
        fcntl.flock(guard, fcntl.LOCK_EX)
        yield


def verified(path, digest):
    """Absent is a cache miss; present but untrusted is always an error."""
    canonical(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return False
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError('archive must be an independent regular file: ' + str(path))
        checksum = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(block)
        after = os.fstat(stream.fileno())
    if (checksum.hexdigest() != digest or
            (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)):
        raise ValueError('archive checksum mismatch: ' + str(path))
    return True


def copy_verified(source, destination, digest):
    """The caller holds both publication locks. Never truncate a final path."""
    if verified(destination, digest):
        return
    if not verified(source, digest):
        raise ValueError('missing verified archive source')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.archive-', delete=False) as output:
            temporary = Path(output.name)
            descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, 'rb') as incoming:
                shutil.copyfileobj(incoming, output, 1024 * 1024)
        verified(temporary, digest)
        temporary.chmod(0o444)
        # Another digest must not replace a same-named run input either.
        if verified(destination, digest):
            return
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def stage(root, directory, name, digest, *, upload=None):
    root, cached = parameters(root, name, digest)
    directory = input_directory(root, directory)
    target = directory / name
    if upload is not None:
        upload = canonical(upload)
        if (upload.parent != directory or
                not re.fullmatch(r'\.upload-[0-9a-f]{32}-' + re.escape(name), upload.name)):
            raise ValueError('upload must be a unique temporary run input')
    with locked(cached.parent / '.lock'), locked(directory / '.archive-stage.lock'):
        cached_present = verified(cached, digest)
        target_present = verified(target, digest)
        if upload is not None:
            if not verified(upload, digest):
                raise ValueError('missing completed archive upload')
            if not cached_present:
                copy_verified(upload, cached, digest)
                cached_present = True
        elif not cached_present and target_present:
            copy_verified(target, cached, digest)
            cached_present = True
        if not cached_present:
            return False
        if not target_present:
            copy_verified(cached, target, digest)
        if upload is not None:
            upload.unlink()
    return True


def seed(root, source, name, digest):
    """Import an explicitly selected existing archive without changing its bytes."""
    _, cached = parameters(root, name, digest)
    source = canonical(source)
    if not verified(source, digest):
        raise ValueError('missing archive seed')
    with locked(cached.parent / '.lock'):
        copy_verified(source, cached, digest)
    return cached


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('stage', 'commit'))
    parser.add_argument('root', type=Path)
    parser.add_argument('directory', type=Path)
    parser.add_argument('name')
    parser.add_argument('sha256')
    parser.add_argument('--upload', type=Path)
    args = parser.parse_args()
    if (args.operation == 'commit') != (args.upload is not None):
        parser.error('only commit requires --upload')
    print(json.dumps(stage(args.root, args.directory, args.name, args.sha256, upload=args.upload)))


if __name__ == '__main__':
    main()
