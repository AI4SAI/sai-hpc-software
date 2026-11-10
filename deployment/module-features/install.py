#!/usr/bin/env python3
"""Install reviewed help/whatis updates for saiuser01's public modulefiles."""
import argparse
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import stat

PACKAGE = Path(__file__).resolve().parent


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(content):
    return hashlib.sha256(content).hexdigest()


def regular(path):
    require(path.resolve() == path and not path.is_symlink(), 'redirected module path: ' + str(path))
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
            'module must be a regular, single-link file: ' + str(path))
    return info


def preflight(package, root, manifest):
    require(manifest['schema'] == 1, 'unsupported manifest')
    uid = pwd.getpwnam(manifest['owner']).pw_uid
    pending, seen = [], set()
    for row in manifest['modules']:
        name = Path(row['path'])
        require(not name.is_absolute() and '..' not in name.parts
                and len(name.parts) == 3 and name.parts[0] in ('apps', 'devtools')
                and not name.name.startswith('.') and str(name) not in seen,
                'unsafe/duplicate module name')
        seen.add(str(name))
        require(row['source'] == 'modulefiles/' + str(name), 'unexpected candidate source')
        source, target = package / row['source'], root / name
        regular(source)
        desired = source.read_bytes()
        require(digest(desired) == row['after_sha256'], 'candidate changed: ' + str(name))
        info = regular(target)
        require(info.st_uid == uid, 'module owner changed: ' + str(target))
        before = target.read_bytes()
        require(digest(before) in (row['before_sha256'], row['after_sha256']),
                'installed module differs from both reviewed versions: ' + str(target))
        for reference in row['readable_files']:
            require(Path(reference).is_file() and os.access(reference, os.R_OK),
                    'advertised local record is unreadable: ' + reference)
        print(str(name) + (': already current' if before == desired else ': documentation update ready'))
        if before != desired:
            pending.append((target, before, desired, info))
    return pending


def install(root, pending, backup):
    backup.mkdir(parents=True, mode=0o700)
    # Save every original before replacing the first module. Backups stay out of /opt.
    for target, before, desired, info in pending:
        current = regular(target)
        require(current.st_uid == info.st_uid and current.st_gid == info.st_gid
                and current.st_mode == info.st_mode and target.read_bytes() == before,
                'module changed during preflight: ' + str(target))
        saved = backup / target.relative_to(root)
        saved.parent.mkdir(parents=True, exist_ok=True)
        with saved.open('xb') as output:
            output.write(before)
            output.flush()
            os.fsync(output.fileno())
    print('Original modulefiles: ' + str(backup), flush=True)
    for target, before, desired, info in pending:
        require(regular(target).st_uid == info.st_uid and target.read_bytes() == before,
                'module changed during installation: ' + str(target))
        temporary = target.with_name('.' + target.name + '.features-' + str(os.getpid()))
        created = False
        try:
            with temporary.open('xb') as output:
                created = True
                output.write(desired)
                # Keep site ownership even when an administrator runs as root.
                os.fchown(output.fileno(), info.st_uid, info.st_gid)
                os.fchmod(output.fileno(), stat.S_IMODE(info.st_mode))
                output.flush()
                os.fsync(output.fileno())
            require(temporary.read_bytes() == desired, 'module copy differs')
            os.replace(temporary, target)
        finally:
            if created and temporary.exists():
                temporary.unlink()
        print('Updated ' + str(target) + ' sha256=' + digest(desired), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--check', action='store_true')
    action.add_argument('--install', action='store_true')
    args = parser.parse_args()
    manifest = json.loads((PACKAGE / 'manifest.json').read_text())
    root = Path(manifest['root'])
    require(root == Path('/opt/modules/modulefiles'), 'unexpected module root')
    if not args.install:
        preflight(PACKAGE, root, manifest)
        return
    backups = Path.home() / 'sai-hpc-module-backups'
    backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (backups / '.module-features.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pending = preflight(PACKAGE, root, manifest)
        if pending:
            backup = backups / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid()))
            install(root, pending, backup)
    print('Inspect with module --ignore_cache whatis <module> and module --ignore_cache help <module>.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit('Module description update stopped: ' + str(error)) from error
