#!/usr/bin/env python3
"""Administrator installation of the accepted, minimal NEPAdapters runtime.

No arguments: read-only verification. --install: publish the accepted runtime
and replace the previous partitioned release recorded in the manifest.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

PACKAGE = Path(__file__).resolve().parent
MODULE = Path('/opt/modules/modulefiles/apps/nepadapters/1.0.5-lammps30Sep2026')
SHARED_PREFIX = Path('/opt/apps/nepadapters/1.0.5-lammps30Sep2026')
PAYLOAD = {'lib/nepadapterscpuplugin.so', 'lib/nepadaptersgpuplugin.so',
           'share/licenses/nepadapters/LICENSE.NEP_CPU'}


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def plain(path):
    require(path.is_absolute() and '..' not in path.parts, 'noncanonical path: ' + str(path))
    require(all(not p.is_symlink() for p in (path, *path.parents)), 'symlink in path: ' + str(path))


def verify_payload(root, files, allow_missing=False):
    plain(root)
    require(set(files) == PAYLOAD, 'unexpected release file list')
    expected = set(files)
    for name in files:
        expected.update(str(p) for p in Path(name).parents if str(p) != '.')
    if allow_missing and not root.exists():
        return
    actual = {str(p.relative_to(root)) for p in root.rglob('*')}
    require(root.is_dir() and (actual <= expected if allow_missing else actual == expected),
            'payload contains missing or extra files: ' + str(root))
    for name in expected - set(files):
        path = root / name
        plain(path)
        require(not path.exists() or path.is_dir(), 'payload directory replaced by a file: ' + str(path))
    for name, row in files.items():
        path = root / name
        plain(path)
        if allow_missing and not path.exists():
            continue
        require(path.is_file() and stat.S_IMODE(path.stat().st_mode) == 0o644
                and path.stat().st_size == row['size'] and digest(path) == row['sha256'],
                'payload differs from accepted release: ' + str(path))


def verify_replacements(release):
    rows = release.get('replaces', [])
    require(isinstance(rows, list) and (not rows or release['schema'] == 2),
            'only a shared release can replace partitioned installations')
    seen = set()
    for row in rows:
        prefix, part = Path(row['prefix']), row['partition']
        plain(prefix)
        require(part in release['partitions'] and prefix.name == part and
                prefix.parent.parent == SHARED_PREFIX.parent and
                re.fullmatch(re.escape(SHARED_PREFIX.name) + r'-r[0-9a-f]{12}', prefix.parent.name) and
                str(prefix) not in seen, 'unexpected previous plugin prefix: ' + str(prefix))
        seen.add(str(prefix))
        # A retry may resume after some old files have already been removed.
        # Every remaining entry must still belong to the exact old release.
        verify_payload(prefix, row['files'], allow_missing=True)
    return rows


def remove_replaced(release):
    rows = verify_replacements(release)
    for row in rows:
        prefix = Path(row['prefix'])
        if not prefix.exists():
            continue
        for name in row['files']:
            (prefix / name).unlink(missing_ok=True)
        directories = {p for name in row['files'] for p in Path(name).parents if str(p) != '.'}
        for relative in sorted(directories, key=lambda p: len(p.parts), reverse=True):
            path = prefix / relative
            if path.exists():
                path.rmdir()
        prefix.rmdir()
        print('Removed superseded plugin: ' + str(prefix))
    for parent in sorted({Path(row['prefix']).parent for row in rows}):
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()


def preflight(package=PACKAGE):
    release = json.loads((package / 'release.json').read_text())
    require(release['schema'] in (1, 2) and release['version'] == '1.0.5'
            and release['source_sha'] == '3ceba8f9b69765ac29d9f2f4f1c4f76cee8ecf37'
            and release['module'] == str(MODULE), 'wrong NEPAdapters release plan')
    require(release['partitions'] and set(release['partitions']) <= {'16V100', '4V100', '8V100V0'},
            'empty/unsupported partition release')
    require(digest(package / 'modulefile') == release['module_sha256'], 'module changed')
    for name, sha in release['checks'].items():
        require(not Path(name).is_absolute() and '..' not in Path(name).parts, 'invalid public check path')
        plain(package / name)
        require(digest(package / name) == sha, 'public validation package changed: ' + name)
    for part, row in release['partitions'].items():
        prefix = Path(row['prefix'])
        plain(prefix)
        if release['schema'] == 2:
            require(prefix == SHARED_PREFIX and row['prefix'] == release['shared']['prefix'] and
                    row['files'] == release['shared']['files'], 'shared plugin differs between partitions')
        else:
            require(prefix.parent.parent == Path('/opt/apps/nepadapters')
                    and prefix.name == part and re.fullmatch(r'1\.0\.5-lammps30Sep2026-r[0-9a-f]{12}', prefix.parent.name),
                    'unexpected plugin installation prefix')
        require(row['accepted'] is True and row['native_multigpu_accepted'] is True,
                'missing SIF/native multi-GPU acceptance')
        require(row['lammps']['identity']['source_sha'] == '8de817dd79bfe4525d5d39246a212d833e6dee07' and
                row['lammps']['identity']['partition'] == part and 'bin/lmp' in row['lammps']['files'] and
                any('liblammps.so' in n for n in row['lammps']['files']), 'missing matching LAMMPS ABI pins')
        for name, sha in row['lammps']['files'].items():
            require(not Path(name).is_absolute() and '..' not in Path(name).parts, 'invalid LAMMPS file path')
            path = Path(row['lammps']['prefix']) / name
            plain(path)
            require(digest(path) == sha, 'installed LAMMPS no longer matches this plugin: ' + str(path))
        verify_payload(package / 'payload' / ('shared' if release['schema'] == 2 else part), row['files'])
        if prefix.exists():
            verify_payload(prefix, row['files'])
        print(part + ': accepted plugin and installed LAMMPS verified')
    plain(MODULE)
    if MODULE.exists():
        require(digest(MODULE) in [release['module_sha256'], *release.get('previous_module_sha256', [])],
                'refusing to overwrite a different module')
    for row in verify_replacements(release):
        if Path(row['prefix']).exists():
            print('Will replace and remove: ' + row['prefix'])
    return release


def mkdir_public(path):
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    path.mkdir(parents=True, exist_ok=True, mode=0o755)
    for directory in missing:
        directory.chmod(0o755)


def install(release):
    verify_replacements(release)
    for part, row in release['partitions'].items():
        prefix = Path(row['prefix'])
        if prefix.exists():
            continue
        plain(prefix)
        mkdir_public(prefix.parent)
        stage = Path(tempfile.mkdtemp(prefix='.' + part + '.install-', dir=prefix.parent))
        stage.chmod(0o755)
        for name in row['files']:
            target = stage / name
            mkdir_public(target.parent)
            payload = 'shared' if release.get('schema') == 2 else part
            shutil.copyfile(PACKAGE / 'payload' / payload / name, target)
            target.chmod(0o644)
        verify_payload(stage, row['files'])
        # mkdir reserves the exact destination without overwriting another install.
        prefix.mkdir(mode=0o700)
        stage.rename(prefix)
        print('Installed ' + str(prefix))
    if not MODULE.exists() or digest(MODULE) != release.get('module_sha256'):
        plain(MODULE)
        mkdir_public(MODULE.parent)
        # Publish the complete module atomically and never replace another file.
        with tempfile.NamedTemporaryFile(prefix='.nepadapters-', dir=MODULE.parent, delete=False) as stream:
            staged_module = Path(stream.name)
            stream.write((PACKAGE / 'modulefile').read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        try:
            staged_module.chmod(0o644)
            if MODULE.exists():
                require(digest(MODULE) in release.get('previous_module_sha256', []),
                        'module changed during installation')
                os.replace(staged_module, MODULE)
            else:
                os.link(staged_module, MODULE)
        finally:
            if staged_module.exists():
                staged_module.unlink()
    if release.get('replaces'):
        verify_payload(SHARED_PREFIX, release['shared']['files'])
        require(digest(MODULE) == release['module_sha256'], 'shared module was not published')
        remove_replaced(release)
    print('Installed module: nepadapters/1.0.5-lammps30Sep2026')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--install', action='store_true')
    actions.add_argument('--check', action='store_true', help='Read-only preflight (default)')
    actions.add_argument('--verify', action='store_true', help='Verify the actual installed files and module')
    args = parser.parse_args()
    try:
        release = preflight()
        if args.install:
            install(release)
        elif args.verify:
            verify_payload(SHARED_PREFIX, release['shared']['files'])
            require(MODULE.is_file() and digest(MODULE) == release['module_sha256'], 'installed module differs')
            print('Installed NEPAdapters 1.0.5 payload and module verified.')
        else:
            print('Preflight passed. Run bash install.sh --install to install.')
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit('Installation stopped: ' + str(error))
