#!/usr/bin/env python3
"""Install the accepted LAMMPS/DeePMD pair at its exact native prefixes.

Default is read-only preflight. --install copies all six accepted runtime
trees and publishes the partition selector. --verify checks installed bytes.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

PACKAGE = Path(__file__).resolve().parent
PROJECT = Path('/home/stardust/sai-hpc-software')
MODULE = Path('/opt/modules/modulefiles/apps/lammps/30Sep2026-deepmd320-auto')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def plain(path):
    require(path.is_absolute() and '..' not in path.parts, 'noncanonical path: ' + str(path))
    for part in [*reversed(path.parents), path]:
        require(not part.is_symlink(), 'symlink in installation path: ' + str(part))


def pinned(path, expected):
    plain(path)
    require(path.is_file() and path.stat().st_size < 64 * 1024**2, 'missing/oversized metadata: ' + str(path))
    require(digest(path) == expected, 'metadata changed: ' + str(path))
    return json.loads(path.read_text())


def verify_tree(root, spec):
    """Verify the complete exported tree, including links and reserved metadata."""
    plain(root)
    manifest = pinned(root / 'share/sai/manifest.json', spec['manifest_sha256'])
    prefix = Path(spec['prefix'])
    require(manifest['entry']['identity'] == spec['identity'], 'native identity changed')
    expected = {}
    for item in manifest['files']:
        absolute = Path('/' + item['path'])
        require(absolute.is_relative_to(prefix) and '..' not in absolute.parts, 'inventory escapes prefix')
        relative = str(absolute.relative_to(prefix))
        require(relative not in expected, 'duplicate inventory path')
        expected[relative] = item
    for name, checksum in spec['metadata_sha256'].items():
        require(name in ('share/sai/manifest.json', 'share/sai/export.json', 'share/sai/README-export.txt'),
                'unknown reserved export metadata')
        require(name not in expected, 'reserved metadata listed in payload')
        expected[name] = dict(kind='file', mode=0o444, sha256=checksum)
    require(set(spec['metadata_sha256']) ==
            {'share/sai/manifest.json', 'share/sai/export.json', 'share/sai/README-export.txt'}, 'incomplete metadata pins')
    seen, pending, files, size = set(), [root], 0, 0
    while pending:
        path = pending.pop()
        name = str(path.relative_to(root))
        row = expected.get(name)
        require(row is not None, 'unlisted file: ' + str(path))
        info = path.lstat()
        kind = ('symlink' if stat.S_ISLNK(info.st_mode) else 'directory' if stat.S_ISDIR(info.st_mode)
                else 'file' if stat.S_ISREG(info.st_mode) else 'special')
        require(kind == row['kind'], 'file type changed: ' + str(path))
        if kind == 'symlink':
            require(os.readlink(path) == row['target'], 'link target changed: ' + str(path))
        else:
            require(stat.S_IMODE(info.st_mode) == row['mode'], 'permissions changed: ' + str(path))
            if kind == 'directory':
                pending.extend(path.iterdir())
            else:
                require(('size' not in row or info.st_size == row['size']) and digest(path) == row['sha256'],
                        'payload changed: ' + str(path))
                files += 1
                size += info.st_size
        seen.add(name)
    require(seen == set(expected), 'missing installed files: ' + str(root))
    return dict(files=files, bytes=size)


def load_release():
    release = json.loads((PACKAGE / 'release.json').read_text())
    require(release['schema'] == 1 and release['version'] == 'stable_30Sep2026', 'wrong release')
    require({d['partition'] for d in release['deliveries']} == {'4V100', '16V100', '8V100V0'}
            and len(release['deliveries']) == 3, 'expected three GPU partitions')
    for delivery in release['deliveries']:
        require({s['identity']['software'] for s in delivery['installations']} == {'lammps', 'deepmd-kit'}
                and len(delivery['installations']) == 2, 'missing paired installation')
        for spec in delivery['installations']:
            identity = spec['identity']
            require(spec['prefix'] == identity['install_prefix'] and
                    Path(spec['prefix']).parts[:4] == ('/', 'opt', 'apps', identity['software']) and
                    Path(spec['prefix']).name == delivery['partition'] == identity['partition'], 'invalid native prefix')
    return release


def preflight(release, project, physical_root=Path('/'), *, installed=False):
    for delivery in release['deliveries']:
        print(delivery['partition'] + ': checking ' + ('installed files' if installed else 'accepted export'), flush=True)
        if not installed:
            publication = pinned(project / delivery['publication'], delivery['publication_sha256'])
            require(all(publication.get(key) is True for key in
                        ('published', 'build_verified', 'scientific_verified', 'performance_verified')) and
                    publication.get('performance_scope') == 'gpu' and
                    publication.get('performance_backends') == ['pt'], 'delivery is not GPU accepted')
            image = project / delivery['image']
            plain(image)
            require(str(image) == publication['artifact'] and digest(image) == delivery['image_sha256'] ==
                    publication['artifact_sha256'], 'SIF changed')
        for spec in delivery['installations']:
            target = physical_root / Path(spec['prefix']).relative_to('/')
            plain(target)
            if installed:
                result = verify_tree(target, spec)
            else:
                require(publication['delivery']['identities'][spec['identity']['software']] == spec['identity'],
                        'export identity differs from publication')
                entry = next(item for item in publication['native_export']['receipt']['installations']
                             if item['expected_install_prefix'] == spec['prefix'])
                require(project / spec['source'] == Path(publication['native_export']['directory']) / entry['path']
                        and spec['manifest_sha256'] == entry['manifest_sha256'], 'export differs from publication')
                result = verify_tree(project / spec['source'], spec)
                if target.exists():
                    verify_tree(target, spec)
            print(f"  {spec['identity']['software']}: {result['files']} files, {result['bytes']/1024**2:.2f} MiB", flush=True)
    module = physical_root / MODULE.relative_to('/')
    plain(module)
    if installed or module.exists():
        require(module.is_file() and stat.S_IMODE(module.stat().st_mode) == 0o644 and
                module.read_bytes() == (PACKAGE / MODULE.name).read_bytes(), 'existing site module differs')


def directories(path):
    plain(path)
    for parent in [*reversed(path.parents), path]:
        if not parent.exists():
            parent.mkdir(mode=0o755)
            parent.chmod(0o755)
        require(parent.is_dir(), 'installation ancestor is not a directory')


def install(release, project, physical_root=Path('/')):
    preflight(release, project, physical_root)
    app_root = physical_root / 'opt/apps'
    directories(app_root)
    lock_path = app_root / '.lammps-30Sep2026-install.lock'
    plain(lock_path)
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for delivery in release['deliveries']:
            for spec in delivery['installations']:
                target = physical_root / Path(spec['prefix']).relative_to('/')
                plain(target)
                if target.exists():
                    verify_tree(target, spec)
                    continue
                directories(target.parent)
                stage = target.with_name('.' + target.name + '.incoming-' + str(os.getpid()))
                require(not stage.exists() and not stage.is_symlink(), 'staging path already exists')
                shutil.copytree(project / spec['source'], stage, symlinks=True)
                verify_tree(stage, spec)
                require(not target.exists() and not target.is_symlink(), 'destination appeared while copying')
                stage.rename(target)
                print('Installed ' + str(target), flush=True)
        module = physical_root / MODULE.relative_to('/')
        directories(module.parent)
        if not module.exists():
            stage = module.with_name('.' + module.name + '.incoming-' + str(os.getpid()))
            with stage.open('xb') as output:
                output.write((PACKAGE / MODULE.name).read_bytes())
                output.flush()
                os.fsync(output.fileno())
            stage.chmod(0o644)
            os.link(stage, module)  # Atomic publish; never replaces another module.
            stage.unlink()
        preflight(release, project, physical_root, installed=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument('--check', action='store_true')
    operation.add_argument('--install', action='store_true')
    operation.add_argument('--verify', action='store_true')
    parser.add_argument('--project', type=Path, default=PROJECT)
    args = parser.parse_args()
    release = load_release()
    if args.install:
        install(release, args.project)
    else:
        preflight(release, args.project, installed=args.verify)
    print('LAMMPS_NATIVE_' + ('INSTALLED' if args.install else 'VERIFIED' if args.verify else 'PREFLIGHT_PASSED'))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit('Installation stopped: ' + str(error)) from error
