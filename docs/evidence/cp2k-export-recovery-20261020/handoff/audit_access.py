#!/usr/bin/env python3
import hashlib
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
from datetime import datetime, timezone

package = Path('/home/stardust/sai-hpc-software/deployment/elpa-2026.02.001-backtransform')
account = pwd.getpwnam('saiuser01')
groups = set(os.getgrouplist(account.pw_name, account.pw_gid))
rows, failures = [], []
paths = set(package.rglob('*')) | {package} | set(package.parents)
for path in list(paths):
    if path.is_symlink():
        target = path.resolve(strict=True)
        if not target.is_relative_to(package):
            raise ValueError('Payload link escapes: ' + str(path))
        paths.add(target)
        paths.update(target.parents)
for path in sorted(paths):
    if path.is_symlink():
        continue
    info = path.stat()
    raw = subprocess.check_output(['getfacl', '-cpn', str(path)], text=True)
    entries = {}
    for line in raw.splitlines():
        if not line or line.startswith('#') or line.startswith('default:'):
            continue
        kind, name, perms = line.split('#')[0].strip().split(':')
        entries[(kind, name)] = sum(bit for flag, bit in [('r',4),('w',2),('x',1)] if flag in perms)
    mask = entries.get(('mask',''), 7)
    if info.st_uid == account.pw_uid:
        effective = entries[('user','')]
    elif ('user', str(account.pw_uid)) in entries:
        effective = entries[('user',str(account.pw_uid))] & mask
    else:
        matched = []
        if info.st_gid in groups:
            matched.append(entries[('group','')])
        matched.extend(value for (kind, name), value in entries.items()
                       if kind == 'group' and name and int(name) in groups)
        effective = 0
        for value in matched:
            effective |= value
        effective = effective & mask if matched else entries[('other','')]
    inside = path == package or path.is_relative_to(package)
    required = (5 if inside else 1) if path.is_dir() else 4
    passed = effective & required == required and effective & 2 == 0
    if inside and stat.S_IMODE(info.st_mode) & 0o022:
        passed = False
    row = dict(path=str(path), mode=oct(stat.S_IMODE(info.st_mode)), uid=info.st_uid,
               gid=info.st_gid, effective=effective, required=required, passed=passed, acl=raw)
    rows.append(row)
    if not passed:
        failures.append(str(path))
release = package / 'release.json'
proof = dict(observed_utc=datetime.now(timezone.utc).isoformat(),
    account=account.pw_name, uid=account.pw_uid, groups=sorted(groups),
    method='Effective POSIX ACL evaluation; no impersonated execution',
    checked_paths=len(rows), symlinks=sum(p.is_symlink() for p in paths),
    release_sha256=hashlib.sha256(release.read_bytes()).hexdigest(),
    passed=not failures, failures=failures, paths=rows)
output=Path('/home/stardust/sai-hpc-software/diagnostics/elpa-backtransform-20261020-access.json')
output.write_text(json.dumps(proof, indent=2, sort_keys=True) + '\n')
print(json.dumps({k:v for k,v in proof.items() if k != 'paths'}, indent=2))
raise SystemExit(bool(failures))
