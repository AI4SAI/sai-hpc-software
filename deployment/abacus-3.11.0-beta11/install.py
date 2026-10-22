#!/usr/bin/env python3
"""Install the accepted ABACUS release at its exact native prefixes.

Default is read-only preflight. --install copies the selected runtime files
and publishes the partition selector. Workflow metadata stays in this package.
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
MODULE = Path('/opt/modules/modulefiles/apps/abacus/3.11.0-beta11-auto')
POLICY = 'abacus-standard-runtime-v1'
VERSION = 'v3.11.0-beta11'
BUILD_ID = VERSION + '-gb0411118d360-r92ecfac1f62a'
PARTITIONS = ('4V100', '16V100', '8V100V0', 'DSPRHBM')


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


def manifest_records(manifest, spec):
    prefix = Path(spec['prefix'])
    require(manifest['entry']['identity'] == spec['identity'], 'native identity changed')
    expected = {}
    for item in manifest['files']:
        absolute = Path('/' + item['path'])
        require(absolute.is_relative_to(prefix) and '..' not in absolute.parts, 'inventory escapes prefix')
        relative = str(absolute.relative_to(prefix))
        require(relative not in expected, 'duplicate inventory path')
        expected[relative] = item
    return expected


def omission(relative):
    parts = Path(relative).parts
    if parts[:2] == ('share', 'sai'):
        return 'workflow metadata and tests remain in the home tree'
    if parts[:1] == ('modulefiles',):
        return 'replaced by the standalone public module'
    if parts[:1] == ('dependencies',) and len(parts) >= 2:
        if parts[1] in ('LibComm-master', 'LibRI-b0eff7a', 'cereal', 'json', 'libnpy-1.0.1'):
            return 'header-only build dependency'
        if 'include' in parts or 'cmake' in parts or 'pkgconfig' in parts:
            return 'private dependency development interface'
        if Path(relative).suffix == '.a':
            return 'private dependency static archive'
        if parts[-1] in ('libjitbackend_test.so', 'libtorchbind_test.so'):
            return 'upstream LibTorch test library'
    if '__pycache__' in parts or Path(relative).suffix in ('.pyc', '.pyo'):
        return 'regenerable Python bytecode'
    return None


def omission_summary(removed):
    summary = {}
    for row in removed:
        value = summary.setdefault(row['reason'], dict(files=0, bytes=0))
        value['files'] += 1
        value['bytes'] += row['bytes']
    return summary


def runtime_records(manifest, spec):
    """Omit only known non-runtime material; preserve public APIs and all data."""
    original = manifest_records(manifest, spec)
    selected, removed = {}, []
    for name, row in original.items():
        require(row['kind'] in ('file', 'directory', 'symlink'), 'special export entry')
        require(type(row['mode']) is int and 0 <= row['mode'] <= 0o777, 'invalid export mode')
        reason = omission(name)
        if reason:
            if row['kind'] != 'directory':
                removed.append(dict(path=name, bytes=row.get('size', 0), reason=reason))
        elif row['kind'] != 'directory':
            selected[name] = dict(row, path=name)
    for name in list(selected):
        for parent in Path(name).parents:
            key = str(parent)
            require(original.get(key, {}).get('kind') == 'directory', 'missing parent directory')
            selected[key] = dict(original[key], path=key)
    prefix = Path(spec['prefix'])
    # Resolve retained links against the projected inventory, not the source
    # filesystem, to catch links into omitted files before anything is copied.
    for name, row in selected.items():
        if row['kind'] != 'symlink':
            continue
        pending, resolved, links = str(prefix / name).split('/'), [], 0
        while pending:
            part = pending.pop(0)
            if part in ('', '.'):
                continue
            if part == '..':
                if resolved:
                    resolved.pop()
                continue
            resolved.append(part)
            path = Path('/' + '/'.join(resolved))
            if path.is_relative_to(prefix):
                entry = selected.get(str(path.relative_to(prefix)))
                require(entry is not None, 'pruning breaks a runtime link: ' + name)
                if entry['kind'] == 'symlink':
                    links += 1
                    require(links <= 40, 'cyclic runtime link: ' + name)
                    resolved.pop()
                    if entry['target'].startswith('/'):
                        resolved = []
                    pending = entry['target'].split('/') + pending
                elif pending:
                    require(entry['kind'] == 'directory', 'link traverses a file')
        target = Path('/' + '/'.join(resolved))
        roots = [prefix, *map(Path, manifest['entry'].get('external_roots', []))]
        require(any(target.is_relative_to(root) for root in roots), 'link escapes declared runtime roots')
    return sorted(selected.values(), key=lambda row: row['path']), sorted(removed, key=lambda row: row['path'])


def runtime_plan(release):
    plan = json.loads((PACKAGE / 'runtime-plan.json').read_text())
    require(plan['schema'] == 1 and plan['policy'] == POLICY and
            plan['release_sha256'] == digest(PACKAGE / 'release.json'), 'wrong runtime deployment plan')
    entries = {row['prefix']: row for row in plan['installations']}
    require(len(entries) == len(plan['installations']), 'duplicate runtime prefix')
    for delivery in release['deliveries']:
        for spec in delivery['installations']:
            entry = entries[spec['prefix']]
            require(entry['identity'] == spec['identity'] and entry['manifest_sha256'] == spec['manifest_sha256'],
                    'runtime plan identity changed')
            paths = [row['path'] for row in entry['files']]
            require(len(paths) == len(set(paths)) and '.' in paths, 'invalid runtime inventory')
            require(all(not Path(name).is_absolute() and '..' not in Path(name).parts and
                        omission(name) is None for name in paths), 'non-runtime or unsafe path in deployment plan')
    return entries


def verify_tree(root, spec):
    """Verify the full original export when explicitly auditing source material."""
    plain(root)
    manifest = pinned(root / 'share/sai/manifest.json', spec['manifest_sha256'])
    expected = manifest_records(manifest, spec)
    for name, checksum in spec['metadata_sha256'].items():
        require(name in ('share/sai/manifest.json', 'share/sai/export.json', 'share/sai/README-export.txt'),
                'unknown reserved export metadata')
        require(name not in expected, 'reserved metadata listed in payload')
        expected[name] = dict(kind='file', mode=0o444, sha256=checksum)
    require(set(spec['metadata_sha256']) ==
            {'share/sai/manifest.json', 'share/sai/export.json', 'share/sai/README-export.txt'}, 'incomplete metadata pins')
    return verify_records(root, expected)


def verify_records(root, expected, *, subset=False):
    plain(root)
    seen, pending, files, size = set(), ([root / name for name in expected] if subset else [root]), 0, 0
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
                if not subset:
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
    require(release['schema'] == 1 and release['version'] == VERSION, 'wrong release')
    require({d['partition'] for d in release['deliveries']} == set(PARTITIONS)
            and len(release['deliveries']) == 4, 'expected four partitions')
    require(release['module_sha256'] == digest(PACKAGE / MODULE.name), 'public module changed')
    for delivery in release['deliveries']:
        require(len(delivery['installations']) == 1, 'expected one ABACUS installation')
        spec = delivery['installations'][0]
        identity = spec['identity']
        require(identity['software'] == 'abacus' and identity['track'] == 'release' and
                identity['source_version'] == VERSION and identity['build_id'] == BUILD_ID and
                spec['prefix'] == identity['install_prefix'] ==
                '/opt/apps/abacus/abacus-release-' + BUILD_ID + '/' + delivery['partition'] and
                delivery['partition'] == identity['partition'], 'invalid release identity/prefix')
        for name in ('publication', 'image'):
            relative = Path(delivery[name])
            require(not relative.is_absolute() and '..' not in relative.parts, 'unsafe release path')
        require(not Path(spec['source']).is_absolute() and '..' not in Path(spec['source']).parts,
                'unsafe export path')
    return release


def preflight(release, project, physical_root=Path('/'), *, installed=False):
    plan = runtime_plan(release)
    for delivery in release['deliveries']:
        print(delivery['partition'] + ': checking ' + ('installed files' if installed else 'accepted export'), flush=True)
        if not installed:
            publication = pinned(project / delivery['publication'], delivery['publication_sha256'])
            require(all(publication.get(key) is True for key in
                        ('published', 'build_verified', 'verified')) and
                    publication['native_export']['roundtrip_verified'] is True, 'delivery is not accepted')
            names = ['multinode_runtime', 'benchmark_pw', 'benchmark_hse', 'benchmark_deepks']
            if delivery['partition'] != 'DSPRHBM':
                names.append('gpu_features')
            require(all(publication[name]['verified'] is True and
                        publication[name]['artifact_sha256'] == delivery['image_sha256']
                        for name in names), 'incomplete acceptance proofs')
            image = project / delivery['image']
            plain(image)
            require(str(image) == publication['artifact'] and digest(image) == delivery['image_sha256'] ==
                    publication['sha256'], 'SIF changed')
        for spec in delivery['installations']:
            expected = {row['path']: row for row in plan[spec['prefix']]['files']}
            target = physical_root / Path(spec['prefix']).relative_to('/')
            plain(target)
            if installed:
                result = verify_records(target, expected)
            else:
                require(publication['identity'] == spec['identity'],
                        'export identity differs from publication')
                entry = next(item for item in publication['native_export']['receipt']['installations']
                             if item['expected_install_prefix'] == spec['prefix'])
                require(project / spec['source'] == Path(publication['native_export']['directory']) / entry['path']
                        and spec['manifest_sha256'] == entry['manifest_sha256'], 'export differs from publication')
                source = project / spec['source']
                manifest = pinned(source / 'share/sai/manifest.json', spec['manifest_sha256'])
                rows, removed = runtime_records(manifest, spec)
                require(rows == plan[spec['prefix']]['files'] and omission_summary(removed) == plan[spec['prefix']]['omitted_summary'],
                        'runtime plan differs from the pinned source inventory')
                result = verify_records(source, expected, subset=True)
                if target.exists():
                    verify_records(target, expected)
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
    plan = runtime_plan(release)
    # The installing account may own the application subdirectories without
    # having write access to /opt/apps itself, as in the CP2K deployment.
    app_root = physical_root / 'opt/apps/abacus'
    directories(app_root)
    # Lock the existing application directory itself: no workflow lock/receipt
    # files need to be left in /opt.
    descriptor = os.open(app_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        for delivery in release['deliveries']:
            for spec in delivery['installations']:
                target = physical_root / Path(spec['prefix']).relative_to('/')
                rows = plan[spec['prefix']]['files']
                expected = {row['path']: row for row in rows}
                plain(target)
                if target.exists():
                    verify_records(target, expected)
                    continue
                directories(target.parent)
                stage = target.with_name('.' + target.name + '.incoming-' + str(os.getpid()))
                require(not stage.exists() and not stage.is_symlink(), 'staging path already exists')
                stage.mkdir(mode=0o755)
                for row in rows:
                    source, destination = project / spec['source'] / row['path'], stage / row['path']
                    if row['kind'] == 'directory':
                        if row['path'] != '.':
                            destination.mkdir(mode=0o755)
                    elif row['kind'] == 'symlink':
                        require(source.is_symlink() and os.readlink(source) == row['target'], 'source link changed')
                        destination.symlink_to(row['target'])
                    else:
                        plain(source)
                        with source.open('rb') as incoming, destination.open('xb') as outgoing:
                            shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
                        destination.chmod(row['mode'])
                for row in reversed(rows):
                    if row['kind'] == 'directory':
                        (stage / row['path']).chmod(row['mode'])
                verify_records(stage, expected)
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
    finally:
        os.close(descriptor)


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
    print('ABACUS_NATIVE_' + ('INSTALLED' if args.install else 'VERIFIED' if args.verify else 'PREFLIGHT_PASSED'))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit('Installation stopped: ' + str(error)) from error
