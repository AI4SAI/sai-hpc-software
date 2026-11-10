#!/usr/bin/env python3
"""Read-only inventory of a site's modulefiles, selected by actual file owner."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat


def inventory(root, owner):
    uid = pwd.getpwnam(owner).pw_uid
    rows, errors = [], []

    def walk(directory):
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as error:
            errors.append({'path': str(directory), 'error': str(error)})
            return
        for entry in entries:
            path = Path(entry.path)
            try:
                meta = path.lstat()
                if stat.S_ISDIR(meta.st_mode):
                    walk(path)
                    continue
                target = path.stat()
                if uid not in (meta.st_uid, target.st_uid):
                    continue
                raw = path.read_bytes()
                source = raw.decode()
                kind = 'module'
                if path.name.startswith('.'):
                    kind = 'hidden/backup'
                elif re.search(r'^source "/home/stardust/sai-hpc-software/modulefiles/', source, re.M):
                    kind = 'beta-gateway'
                rows.append({
                    'path': str(path.relative_to(root)), 'kind': kind,
                    'sha256': hashlib.sha256(raw).hexdigest(),
                    'symlink': os.readlink(path) if path.is_symlink() else None,
                    'has_direct_whatis': bool(re.search(r'^(module-whatis\s|whatis\()', source, re.M)),
                    'has_direct_help': bool(re.search(r'^(proc ModulesHelp\s|help\()', source, re.M)),
                })
            except (OSError, UnicodeError) as error:
                errors.append({'path': str(path), 'error': str(error)})

    walk(root)
    return {'root': str(root), 'owner': owner, 'rows': rows, 'errors': errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/opt/modules/modulefiles'))
    parser.add_argument('--owner', default='saiuser01')
    args = parser.parse_args()
    result = inventory(args.root, args.owner)
    print(json.dumps(result, indent=2))
    # An inaccessible directory means completeness has not been established.
    if result['errors']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
