#!/usr/bin/env python3
"""Install a new optional fuse2fs version; never replace a site driver/config."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys
import tempfile

PACKAGE = Path(__file__).resolve().parent
VERSION = '1.47.4-sai1'
PREFIX = Path('/opt/devtools/fuse2fs') / VERSION
MODULE = Path('/opt/modules/modulefiles/devtools/fuse2fs/1.47.4-sai')
SITE_CONFIG = Path('/opt/devtools/apptainer/1.4.4/etc/apptainer/apptainer.conf')
BINARY_SHA256 = '46c1a8c8f42ab2419ea88072e41d89903c453d93f52485b5baddea5ca238bb10'
MODES = {'bin/fuse2fs': 0o555, 'etc/apptainer.conf': 0o444,
         'share/licenses/e2fsprogs/NOTICE': 0o444, 'share/man/man1/fuse2fs.1': 0o444}


def require(value, message):
    if not value:
        raise ValueError(message)


def hash_bytes(content):
    return hashlib.sha256(content).hexdigest()


def digest(path):
    return hash_bytes(Path(path).read_bytes())


def plain(path):
    require(path.is_absolute() and '..' not in path.parts, 'Noncanonical path: ' + str(path))
    for entry in (*path.parents, path):
        require(not entry.is_symlink(), 'Symlink in deployment path: ' + str(entry))


def verify_tree(root, files):
    plain(root)
    require(root.is_dir(), 'Missing payload: ' + str(root))
    directories = {'.'}
    for name in files:
        directories.update(str(p) for p in Path(name).parents)
    expected = set(files) | (directories - {'.'})
    require({str(p.relative_to(root)) for p in root.rglob('*')} == expected,
            'Unexpected/missing payload paths: ' + str(root))
    for name in directories:
        path = root / name
        require(path.is_dir() and not path.is_symlink() and
                stat.S_IMODE(path.stat().st_mode) == 0o755, 'Wrong directory mode/type: ' + str(path))
    for name, row in files.items():
        path = root / name
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == row['mode']
                and info.st_size == row['size'] and digest(path) == row['sha256'],
                'Payload differs from release: ' + str(path))


def release_plan():
    release = json.loads((PACKAGE / 'release.json').read_text())
    require(release['schema'] == 1 and release['prefix'] == str(PREFIX)
            and release['module'] == str(MODULE), 'Unexpected installation target')
    require(set(release['files']) == set(MODES) and
            all(row['mode'] == MODES[name] for name, row in release['files'].items()),
            'Unexpected public file list or permissions')
    require(release['files']['bin/fuse2fs']['sha256'] == BINARY_SHA256, 'Wrong driver identity')
    require(digest(PACKAGE / 'modulefile') == release['module_sha256'], 'Module changed')
    require(digest(SITE_CONFIG) == release['site_config_sha256'], 'Site config changed; regenerate and revalidate')
    return release


def effective_access(path, account):
    """POSIX ACL evaluation, including named-user priority and the ACL mask."""
    groups = set(os.getgrouplist(account.pw_name, account.pw_gid))
    raw = subprocess.check_output(['getfacl', '-cpn', '--', str(path)], text=True)
    acl = {}
    for line in raw.splitlines():
        if not line or line.startswith(('#', 'default:')):
            continue
        kind, name, flags = line.split('#')[0].strip().split(':')
        acl[kind, name] = sum(bit for flag, bit in (('r', 4), ('w', 2), ('x', 1)) if flag in flags)
    info = path.stat()
    mask = acl.get(('mask', ''), 7)
    if info.st_uid == account.pw_uid:
        return acl['user', '']
    if ('user', str(account.pw_uid)) in acl:
        return acl['user', str(account.pw_uid)] & mask
    matches = [bits for (kind, name), bits in acl.items() if kind == 'group' and
               (int(name) in groups if name else info.st_gid in groups)]
    bits = 0
    for match in matches:
        bits |= match
    return bits & mask if matches else acl['other', '']


def audit(account_name, files):
    account = pwd.getpwnam(account_name)
    wanted = {}
    for path in [PACKAGE / 'install.py', PACKAGE / 'install.sh', PACKAGE / 'release.json',
                 PACKAGE / 'modulefile', *(PACKAGE / 'payload' / name for name in files)]:
        wanted[path] = 4
    for path in (PREFIX, MODULE):
        plain(path)
        ancestor = path.parent
        while not ancestor.exists():
            ancestor = ancestor.parent
        wanted[ancestor] = 3  # directory search and creation
    for path in list(wanted):
        for parent in path.parents:
            wanted.setdefault(parent, 1)
    rows = []
    for path, bits in sorted(wanted.items()):
        plain(path)
        effective = effective_access(path, account)
        rows.append(dict(path=str(path), required=bits, effective=effective,
                         passed=effective & bits == bits))
    return dict(account=account_name, method='Effective POSIX ACLs; no impersonated execution',
                captured_at=datetime.now(timezone.utc).isoformat(),
                passed=all(row['passed'] for row in rows), paths=rows)


def rename_new(source, target):
    """Reserve exclusively, then atomically replace our own empty directory.

    Site Lustre rejects renameat2(RENAME_NOREPLACE) with EINVAL. mkdir provides
    exclusive reservation, including against another instance of this installer.
    The new module is published only after the complete payload is in place.
    """
    target.mkdir(mode=0o700)
    reservation = target.stat()
    current = target.lstat()
    require(stat.S_ISDIR(current.st_mode) and
            (current.st_dev, current.st_ino) == (reservation.st_dev, reservation.st_ino)
            and not any(target.iterdir()), 'Installation reservation changed: ' + str(target))
    source.rename(target)


def install_files(release):
    # Validate all existing destinations before publishing either artifact.
    for target in (PREFIX, MODULE):
        plain(target)
    if PREFIX.exists():
        verify_tree(PREFIX, release['files'])
    if MODULE.exists():
        require(MODULE.is_file() and digest(MODULE) == release['module_sha256']
                and stat.S_IMODE(MODULE.stat().st_mode) == 0o644,
                'Refusing to replace an existing module: ' + str(MODULE))
    if not PREFIX.exists():
        PREFIX.parent.mkdir(mode=0o755, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.' + PREFIX.name + '.install-', dir=PREFIX.parent))
        stage.chmod(0o755)
        for name, row in release['files'].items():
            source, target = PACKAGE / 'payload' / name, stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            content = source.read_bytes()
            require(hash_bytes(content) == row['sha256'], 'Source changed while staging: ' + name)
            with target.open('xb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            target.chmod(row['mode'])
        for directory in (p for p in stage.rglob('*') if p.is_dir()):
            directory.chmod(0o755)
        verify_tree(stage, release['files'])
        rename_new(stage, PREFIX)
    if not MODULE.exists():
        MODULE.parent.mkdir(mode=0o755, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.' + MODULE.name + '.', dir=MODULE.parent)
        with os.fdopen(fd, 'wb') as stream:
            content = (PACKAGE / 'modulefile').read_bytes()
            require(hash_bytes(content) == release['module_sha256'], 'Module source changed')
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.link(temporary, MODULE)  # atomic no-replace publication
        os.unlink(temporary)


def verify_installed(release):
    verify_tree(PREFIX, release['files'])
    plain(MODULE)
    require(MODULE.is_file() and digest(MODULE) == release['module_sha256']
            and stat.S_IMODE(MODULE.stat().st_mode) == 0o644, 'Installed module changed/missing')
    subprocess.run([str(PREFIX / 'bin/fuse2fs'), '-V'], check=True)
    loader = subprocess.check_output(['ldd', str(PREFIX / 'bin/fuse2fs')], text=True)
    require('not found' not in loader, 'Missing driver runtime library')
    print(loader)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--check', action='store_true')
    modes.add_argument('--install', action='store_true')
    modes.add_argument('--verify', action='store_true')
    parser.add_argument('--account', default='saiuser01', help='Read-only permission audit account')
    parser.add_argument('--report', type=Path, help='Write a new permissions report outside /opt')
    args = parser.parse_args()
    release = release_plan()
    if not args.verify:
        verify_tree(PACKAGE / 'payload', release['files'])
        report = audit(args.account, release['files'])
        if args.report:
            require(not args.report.absolute().is_relative_to(Path('/opt')), 'Keep reports outside /opt')
            with args.report.open('x') as stream:
                json.dump(report, stream, indent=2, sort_keys=True)
                stream.write('\n')
        for row in report['paths']:
            if not row['passed']:
                print(json.dumps(row))
        require(report['passed'], 'Required source/target access is missing for ' + args.account)
        print(f"{args.account}: source read/traverse and target creation permissions passed")
        if args.install:
            for path in (PREFIX.parent, MODULE.parent):
                while not path.exists():
                    path = path.parent
                require(os.access(path, os.W_OK | os.X_OK), 'Current account cannot install in ' + str(path))
            previous_umask = os.umask(0o022)
            try:
                install_files(release)
            finally:
                os.umask(previous_umask)
        else:
            # A pre-existing differing destination must fail the preflight too.
            for target in (PREFIX, MODULE):
                plain(target)
            if PREFIX.exists():
                verify_tree(PREFIX, release['files'])
            if MODULE.exists():
                require(MODULE.is_file() and digest(MODULE) == release['module_sha256']
                        and stat.S_IMODE(MODULE.stat().st_mode) == 0o644, 'Existing module differs')
            print('Payload verified. Install as saiuser01: bash install.sh --install')
            return
    verify_installed(release)
    print('Public files verified: ' + str(PREFIX))
    print(f'Module: fuse2fs/{MODULE.name} (load apptainer/1.4.4 first)')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit('Installation stopped: ' + str(error))
