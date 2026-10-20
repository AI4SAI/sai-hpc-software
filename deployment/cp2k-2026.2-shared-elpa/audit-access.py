#!/usr/bin/env python3
"""Audit an administrator's effective read/traverse access to the prepared sources."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import subprocess


def audit(package, account_name):
    account = pwd.getpwnam(account_name)
    groups = set(os.getgrouplist(account.pw_name, account.pw_gid))
    release = json.loads((package / 'release.json').read_text())
    project = package.parents[1]
    paths = {path: (5 if path.is_dir() else 4) for path in (package, *package.rglob('*'))
             if not path.is_symlink()}
    for entry in release['deliveries']:
        source = project / 'containers/software/cp2k/release' / entry['build_id'] / entry['partition'] / (entry['run_id'] + '.native')
        receipt = json.loads((package / (entry['partition'] + '-receipt.json')).read_text())
        for row in receipt['files']:
            if row['kind'] != 'symlink':
                paths[source / row['path']] = 5 if row['kind'] == 'directory' else 4
        for path in (source / 'share/sai/manifest.json', source / 'share/sai/export.json',
                     source.with_suffix('.json'), source.with_suffix('.sif')):
            paths[path] = 4
    for path in list(paths):
        for parent in path.parents:
            paths.setdefault(parent, 1)
    records = []
    ordered = sorted(paths)
    for start in range(0, len(ordered), 200):
        raw = subprocess.check_output(['getfacl', '-pn', *map(str, ordered[start:start+200])], text=True)
        for block in raw.strip().split('\n\n'):
            lines = block.splitlines()
            path = Path(next(line.removeprefix('# file: ') for line in lines if line.startswith('# file: ')))
            entries = {}
            for line in lines:
                if not line or line.startswith(('#', 'default:')):
                    continue
                kind, name, permissions = line.split('#')[0].strip().split(':')
                entries[kind, name] = sum(bit for flag, bit in (('r', 4), ('w', 2), ('x', 1)) if flag in permissions)
            info = path.stat()
            mask = entries.get(('mask', ''), 7)
            if info.st_uid == account.pw_uid:
                effective = entries['user', '']
            elif ('user', str(account.pw_uid)) in entries:
                effective = entries['user', str(account.pw_uid)] & mask
            else:
                matched = [value for (kind, name), value in entries.items() if kind == 'group' and
                           ((name and int(name) in groups) or (not name and info.st_gid in groups))]
                effective = 0
                for value in matched:
                    effective |= value
                effective = effective & mask if matched else entries['other', '']
            passed = effective & paths[path] == paths[path] and not effective & 2
            records.append(dict(path=str(path), required=paths[path], effective=effective, passed=passed))
    return dict(captured_at=datetime.now(timezone.utc).isoformat(), account=account_name,
                method='Effective POSIX ACL evaluation; no impersonated execution',
                release_sha256=hashlib.sha256((package / 'release.json').read_bytes()).hexdigest(),
                passed=all(row['passed'] for row in records), checked_paths=len(records), paths=records)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--account', default='saiuser01')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = audit(args.package.absolute(), args.account)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write('\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'paths'}, indent=2))
    for row in result['paths']:
        if not row['passed']:
            print(json.dumps(row))
    raise SystemExit(not result['passed'])
