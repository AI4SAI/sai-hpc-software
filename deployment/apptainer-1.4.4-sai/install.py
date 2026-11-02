#!/usr/bin/env python3
"""Administrator-controlled, reversible hotfix of Apptainer 1.4.4's starter."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

HERE = Path(__file__).resolve().parent
PREFIX = Path('/opt/devtools/apptainer/1.4.4')


def digest(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('expected a regular file: ' + str(path))
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            h.update(block)
    return h.hexdigest()


def check_file(path, expected):
    if digest(path) != expected:
        raise ValueError('checksum differs: ' + str(path))


def atomic_copy(source, destination, expected, mode=0o755):
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.starter-grace-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            with source.open('rb') as src:
                shutil.copyfileobj(src, stream)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
            check_file(temporary, expected)
            os.replace(temporary, destination)
            fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if temporary.exists():
                temporary.unlink()


def deploy(action, here=HERE, prefix=PREFIX):
    manifest = json.loads((here / 'manifest.json').read_text())
    if action != 'rollback':
        proof = json.loads((here / 'qualification.json').read_text())
        required = {'plain-small-a', 'plain-small-b', 'plain-build-size', 'delayed-fsync'}
        if (proof.get('verified') is not True or proof.get('starter_sha256') != manifest['starter_sha256']
                or {case['label'] for case in proof.get('cases', [])} != required
                or any(case.get('data_verified') is not True or case.get('fsck_returncode') != 0
                       or case.get('cleanup_failure') is not False or case.get('timeout_warning') is not False
                       for case in proof['cases'])):
            raise ValueError('runtime has not passed the complete overlay qualification')
        check_file(here / 'graceful-driver-stop.patch', manifest['patch_sha256'])
    check_file(prefix / 'bin/apptainer', manifest['site_cli_sha256'])
    source = here / 'payload/starter'
    if action in ('check', 'install'):
        check_file(source, manifest['starter_sha256'])
    target = prefix / 'libexec/apptainer/bin/starter'
    old = manifest['site_starter_sha256']
    new = manifest['starter_sha256']
    current = digest(target)
    if current not in (old, new):
        raise ValueError('installed starter is neither the qualified original nor this hotfix')
    if target.stat().st_mode & 0o6022:
        raise ValueError('setuid, setgid or group/other-writable starter is unsupported')
    backup = target.with_name('starter.before-sai-grace-' + old[:16])
    receipt = prefix / 'share/sai/driver-stop-grace.json'
    receipt_directory = next(path for path in receipt.parents if path.exists())
    writable = (os.access(target.parent, os.W_OK | os.X_OK)
                and os.access(receipt_directory, os.W_OK | os.X_OK))
    result = dict(prefix=str(prefix), current_sha256=current, target_sha256=new,
                  normal_stop_grace_seconds=30 if current == new else 1,
                  backup=str(backup), action=action, installation_directories_writable=writable)
    if action == 'check':
        return result
    if action == 'verify':
        check_file(target, new)
        if json.loads(receipt.read_text()) != manifest:
            raise ValueError('installed hotfix receipt differs')
        return dict(result, verified=True)
    if not writable:
        raise PermissionError('administrator write access is required for ' + str(target.parent)
                              + ' and ' + str(receipt_directory))
    with (target.parent / '.sai-driver-stop.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        check_file(target, current)
        if receipt.exists() and json.loads(receipt.read_text()) != manifest:
            raise ValueError('installed receipt belongs to a different hotfix')
        if action == 'install':
            if current == old:
                if backup.exists():
                    check_file(backup, old)
                else:
                    atomic_copy(target, backup, old, 0o555)
                atomic_copy(source, target, new)
            receipt.parent.mkdir(parents=True, exist_ok=True)
            atomic_copy(here / 'manifest.json', receipt, digest(here / 'manifest.json'), 0o644)
            check_file(target, new)
            return dict(result, installed=True, current_sha256=new, normal_stop_grace_seconds=30)
        if action != 'rollback':
            raise ValueError('unknown installation action')
        if current == new:
            check_file(backup, old)
            atomic_copy(backup, target, old)
        if receipt.exists():
            if json.loads(receipt.read_text()) != manifest:
                raise ValueError('refusing to remove a different installation receipt')
            receipt.unlink()
        check_file(target, old)
        return dict(result, rolled_back=True, current_sha256=old, normal_stop_grace_seconds=1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    for name in ('install', 'verify', 'rollback'):
        group.add_argument('--' + name, action='store_const', const=name, dest='action')
    args = parser.parse_args()
    print(json.dumps(deploy(args.action or 'check'), indent=2))


if __name__ == '__main__':
    main()
