#!/usr/bin/env python3
"""Select SAI NCCL in the existing DeePMD 3.2.0 module; default is read-only."""
import argparse
from datetime import datetime
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

PACKAGE = Path(__file__).resolve().parent
TARGET = Path('/opt/modules/modulefiles/apps/deepmd-kit/3.2.0')
NCCL = Path('/opt/devtools/nvidia/nccl_2.29.3_cuda12.9_sai_v2.29.3-1-sai.2/lib/libnccl.so.2')
ORIGINAL_SHA256 = '54688197265939145c4b82eec51468575a1e816c0441e95ec7de47ddcf0fd79d'
CANDIDATE_SHA256 = '6634309333c6761541e0aa7ee6a979b154ba273c837fe4095bc519b98d39238a'
NCCL_SHA256 = 'af89e7e8f5a25daacc0831cb423faee23fa03407158458be45eb242e81b1193f'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def preflight(target=TARGET, package=PACKAGE, library=NCCL):
    original = (package / 'original/3.2.0').read_bytes()
    candidate = (package / 'modulefiles/deepmd-kit/3.2.0').read_bytes()
    require(digest(original) == ORIGINAL_SHA256, 'packaged original changed')
    require(digest(candidate) == CANDIDATE_SHA256, 'packaged candidate changed')
    require(target.resolve() == target and not target.is_symlink(), 'module path is redirected')
    info = target.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'module is not a regular single-link file')
    current = target.read_bytes()
    require(current in (original, candidate), 'installed module changed; refusing to overwrite newer edits')
    with library.open('rb') as stream:
        require(hashlib.file_digest(stream, 'sha256').hexdigest() == NCCL_SHA256,
                'SAI NCCL differs from the tested binary')
    return original, candidate, current, info


def replace(target, before, desired, info):
    require(target.read_bytes() == before, 'module changed after preflight')
    fd, name = tempfile.mkstemp(prefix='.3.2.0-nccl-', dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(desired)
            # Root retains site ownership. A group-authorized module maintainer
            # owns their replacement, just like a new module in this directory.
            if os.geteuid() == 0:
                os.fchown(output.fileno(), info.st_uid, info.st_gid)
            os.fchmod(output.fileno(), stat.S_IMODE(info.st_mode))
            output.flush()
            os.fsync(output.fileno())
        require(target.read_bytes() == before, 'module changed before replacement')
        os.replace(temporary, target)
        directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--check', action='store_true')
    action.add_argument('--install', action='store_true')
    action.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    original, candidate, current, info = preflight()
    print('SAI NCCL SHA256 verified; module is ' + ('updated' if current == candidate else 'original'))
    if not args.install and not args.rollback:
        print(''.join(difflib.unified_diff(original.decode().splitlines(True), candidate.decode().splitlines(True),
                                         fromfile=str(TARGET), tofile=str(TARGET) + ' (candidate)')), end='')
        print('Install with --install; restore the reviewed original with --rollback.')
        return
    desired = original if args.rollback else candidate
    if current == desired:
        print('Already at the requested version; no change.')
        return
    require(os.access(TARGET.parent, os.W_OK | os.X_OK),
            'run as an administrator with write access to ' + str(TARGET.parent))
    backup_root = Path.home() / 'sai-module-backups/deepmd-nccl-20261115'
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (backup_root / '.install.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        original, candidate, current, info = preflight()
        desired = original if args.rollback else candidate
        if current == desired:
            print('Already at the requested version; no change.')
            return
        backup = backup_root / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid()))
        backup.mkdir(mode=0o700)
        (backup / '3.2.0').write_bytes(current)
        (backup / 'metadata.json').write_text(json.dumps(dict(path=str(TARGET), sha256=digest(current),
            uid=info.st_uid, gid=info.st_gid, mode=stat.S_IMODE(info.st_mode)), indent=2) + '\n')
        replace(TARGET, current, desired, info)
        require(TARGET.read_bytes() == desired, 'installed content differs from the reviewed file')
        print('Backup: ' + str(backup))
        print('Updated ' + str(TARGET) + '; sha256=' + digest(desired))
        print('New jobs should reload deepmd-kit/3.2.0. Existing running processes are unaffected.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit('DeePMD module update stopped: ' + str(error)) from error
