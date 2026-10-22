#!/usr/bin/env python3
"""Audit, or grant read/traverse access to, this release's exact source files.

Run as the source owner. --apply adds named-user ACLs only to blocked source
paths; it never grants write access, changes /opt, or opens unrelated runs.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys

sys.dont_write_bytecode = True
import install as deployment


def collect(package, project):
    release = json.loads((package / 'release.json').read_text())
    plan = json.loads((package / 'runtime-plan.json').read_text())
    reference = json.loads((package / 'native-reference.json').read_text())
    paths, exports = {}, []

    def need(path, bits):
        deployment.require(path.is_absolute() and '..' not in path.parts, 'noncanonical source path')
        paths[path] = paths.get(path, 0) | bits

    for path in (package, *package.rglob('*')):
        if not path.is_symlink():
            need(path, 5 if path.is_dir() else 4)
    entries = {entry['prefix']: entry for entry in plan['installations']}
    for delivery in release['deliveries']:
        for name in ('publication', 'image'):
            need(project / delivery[name], 4)
        for spec in delivery['installations']:
            source = project / spec['source']
            exports.append(source)
            for row in entries[spec['prefix']]['files']:
                if row['kind'] != 'symlink':
                    need(source / row['path'], 5 if row['kind'] == 'directory' else 4)
            for name in spec['metadata_sha256']:
                need(source / name, 4)
    for ref in reference['partitions'].values():
        for name in ref['files']:
            need(project / name, 4)
    targets = {Path('/opt/apps/abacus'), deployment.MODULE.parent}
    for path in targets:
        need(path, 7)
    for path in list(paths):
        for parent in path.parents:
            paths.setdefault(parent, 1)
    for path in paths:
        deployment.require(not path.is_symlink(), 'symlink in source path: ' + str(path))
    return paths, exports, targets


def acl_records(paths, account):
    groups = set(os.getgrouplist(account.pw_name, account.pw_gid))
    result, ordered = {}, sorted(paths)
    for start in range(0, len(ordered), 200):
        raw = subprocess.check_output(['getfacl', '-pn', '--', *map(str, ordered[start:start+200])], text=True)
        for block in raw.strip().split('\n\n'):
            lines = block.splitlines()
            path = Path(next(line.removeprefix('# file: ') for line in lines if line.startswith('# file: ')))
            entries = {}
            for line in lines:
                if not line or line.startswith(('#', 'default:')):
                    continue
                kind, name, value = line.split('#')[0].strip().split(':')
                entries[kind, name] = sum(bit for flag, bit in (('r', 4), ('w', 2), ('x', 1)) if flag in value)
            info = path.stat()
            mask = entries.get(('mask', ''), 7)
            if info.st_uid == account.pw_uid:
                effective = entries['user', '']
            elif ('user', str(account.pw_uid)) in entries:
                effective = entries['user', str(account.pw_uid)] & mask
            else:
                matches = [value for (kind, name), value in entries.items() if kind == 'group' and
                           ((name and int(name) in groups) or (not name and info.st_gid in groups))]
                effective = 0
                for value in matches:
                    effective |= value
                effective = effective & mask if matches else entries['other', '']
            result[path] = dict(path=str(path), required=paths[path], effective=effective,
                                passed=effective & paths[path] == paths[path], owner=info.st_uid,
                                mode=info.st_mode & 0o777, acl=block, entries=entries)
    deployment.require(set(result) == set(paths), 'incomplete ACL audit')
    return result


def permissions(bits):
    return ''.join(flag if bits & bit else '-' for flag, bit in (('r', 4), ('w', 2), ('x', 1)))


def grant(records, account, project, exports):
    changes = []
    key = ('user', str(account.pw_uid))
    for path, row in records.items():
        if row['passed']:
            continue
        deployment.require(path.is_relative_to(project) and row['owner'] == os.getuid(),
                           'blocked path is outside owned project sources: ' + str(path))
        deployment.require(not row['required'] & 2 and row['owner'] != account.pw_uid,
                           'source access may only grant another account read/traverse')
        entries = row['entries']
        old_mask = entries.get(('mask', ''), entries['group', ''])
        wanted = entries.get(key, 0) | row['required']
        new_mask = old_mask | wanted
        for principal, bits in entries.items():
            if principal == key or not (principal[0] == 'group' or principal[0] == 'user' and principal[1]):
                continue
            deployment.require(bits & old_mask == bits & new_mask,
                               'mask expansion would grant another principal access: ' + str(path))
        if any(path.is_relative_to(source) for source in exports):
            deployment.require(new_mask == (row['mode'] >> 3 & 7),
                               'ACL would alter a frozen export mode: ' + str(path))
        changes.append(dict(path=str(path), before=row['acl'], grant=f'u:{account.pw_uid}:{permissions(wanted)},m::{permissions(new_mask)}'))
    # Validate every proposed change before applying any of them.
    for change in changes:
        subprocess.run(['setfacl', '--no-mask', '-m', change['grant'], '--', change['path']], check=True)
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', default='saiuser01')
    parser.add_argument('--project', type=Path, default=deployment.PROJECT)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    deployment.require(not args.report.exists(), 'report already exists')
    account = pwd.getpwnam(args.account)
    paths, exports, targets = collect(deployment.PACKAGE, args.project)
    before = acl_records(paths, account)
    changes = grant(before, account, args.project, exports) if args.apply else []
    after = acl_records(paths, account) if changes else before
    for change in changes:
        change['after'] = after[Path(change['path'])]['acl']
    records = [{k: v for k, v in row.items() if k not in ('entries', 'acl')} for row in after.values()]
    writable_sources = [row['path'] for row in records
                        if Path(row['path']).is_relative_to(args.project) and row['effective'] & 2]
    report = dict(captured_at=datetime.now(timezone.utc).isoformat(), account=args.account,
                  method='Effective POSIX ACL evaluation; no impersonated execution',
                  release_sha256=deployment.digest(deployment.PACKAGE / 'release.json'),
                  runtime_plan_sha256=deployment.digest(deployment.PACKAGE / 'runtime-plan.json'),
                  passed=all(row['passed'] for row in records) and not writable_sources,
                  checked_paths=len(records), source_writable_paths=writable_sources,
                  changes=changes, targets=[after[p]['path'] for p in sorted(targets)], paths=records)
    with args.report.open('x') as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write('\n')
    print(json.dumps({k: v for k, v in report.items() if k not in ('paths', 'changes')}, indent=2))
    print('ACL changes:', len(changes))
    for row in records:
        if not row['passed']:
            print(json.dumps(row))
    raise SystemExit(not report['passed'])


if __name__ == '__main__':
    main()
