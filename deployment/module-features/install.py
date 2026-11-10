#!/usr/bin/env python3
"""Update only the whatis descriptions of the four installed release modules."""
import argparse
from datetime import datetime
import hashlib
import os
from pathlib import Path
import stat

DEPLOYMENT = Path(__file__).resolve().parent.parent
MODULES = (
    ('abacus-3.11.0-beta12/3.11.0-beta12-auto', 'abacus/3.11.0-beta12-auto'),
    ('cp2k-2026.2-shared-elpa/2026.2-auto', 'cp2k/2026.2-auto'),
    ('lammps-30Sep2026/30Sep2026-deepmd320-auto', 'lammps/30Sep2026-deepmd320-auto'),
    ('qe-7.6/7.6-auto', 'qe/7.6-auto'),
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def runtime_body(content):
    return b''.join(line for line in content.splitlines(keepends=True)
                    if not line.startswith(b'module-whatis '))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--check', action='store_true')
    action.add_argument('--install', action='store_true')
    args = parser.parse_args()
    pending = []
    for source, name in MODULES:
        desired = (DEPLOYMENT / source).read_bytes()
        target = Path('/opt/modules/modulefiles/apps') / name
        require(target.is_file() and not target.is_symlink(), 'missing regular module: ' + str(target))
        before = target.read_bytes()
        require(runtime_body(before) == runtime_body(desired),
                'module runtime differs; refusing a description-only update: ' + str(target))
        print(name + (': already current' if before == desired else ': descriptions only'))
        if before != desired:
            pending.append((target, before, desired, stat.S_IMODE(target.stat().st_mode)))
    if not args.install or not pending:
        return
    # Keep backups out of /opt, in the installing account's home directory.
    backup = Path.home() / 'sai-hpc-module-backups' / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid()))
    backup.mkdir(parents=True, mode=0o700)
    for target, before, desired, mode in pending:
        require(target.read_bytes() == before and not target.is_symlink(), 'module changed during preflight')
        saved = backup / target.parent.name / target.name
        saved.parent.mkdir(exist_ok=True)
        with saved.open('xb') as output:
            output.write(before)
        temporary = target.with_name('.' + target.name + '.features-' + str(os.getpid()))
        created = False
        try:
            with temporary.open('xb') as output:
                created = True
                output.write(desired)
                output.flush()
                os.fsync(output.fileno())
            temporary.chmod(mode)
            require(temporary.read_bytes() == desired, 'module copy differs')
            os.replace(temporary, target)
        finally:
            if created and temporary.exists():
                temporary.unlink()
        print('Updated ' + str(target) + ' sha256=' + hashlib.sha256(desired).hexdigest())
    print('Original modulefiles: ' + str(backup))
    print('Inspect with module --ignore_cache whatis <module>.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit('Module description update stopped: ' + str(error)) from error
