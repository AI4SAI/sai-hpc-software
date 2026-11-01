#!/usr/bin/env python3
"""Administrator installation of the accepted, minimal NEPAdapters runtime.

No arguments: read-only verification. --install: install new prefixes and
publish the addon module. Existing differing files are never overwritten.
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
MODULE = Path('/opt/modules/modulefiles/apps/nepadapters/1.0.4-lammps30Sep2026')
SHARED_PREFIX = Path('/opt/apps/nepadapters/1.0.4-lammps30Sep2026')
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


def verify_payload(root, files):
    plain(root)
    require(set(files) == PAYLOAD, 'unexpected release file list')
    expected = set(files)
    for name in files:
        expected.update(str(p) for p in Path(name).parents if str(p) != '.')
    require(root.is_dir() and {str(p.relative_to(root)) for p in root.rglob('*')} == expected,
            'payload contains missing or extra files: ' + str(root))
    for name, row in files.items():
        path = root / name
        plain(path)
        require(path.is_file() and stat.S_IMODE(path.stat().st_mode) == 0o644
                and path.stat().st_size == row['size'] and digest(path) == row['sha256'],
                'payload differs from accepted release: ' + str(path))


def preflight(package=PACKAGE):
    release = json.loads((package / 'release.json').read_text())
    require(release['schema'] in (1, 2) and release['version'] == '1.0.4'
            and release['source_sha'] == 'c934e71444cb4099d63dff98478f02c86373b061'
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
                    and prefix.name == part and re.fullmatch(r'1\.0\.4-lammps30Sep2026-r[0-9a-f]{12}', prefix.parent.name),
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
    print('Installed module: nepadapters/1.0.4-lammps30Sep2026')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true')
    args = parser.parse_args()
    try:
        release = preflight()
        if args.install:
            install(release)
        else:
            print('Preflight passed. Run bash install.sh --install to install.')
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit('Installation stopped: ' + str(error))
