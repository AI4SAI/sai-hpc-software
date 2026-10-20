#!/usr/bin/env python3
"""Deploy only the runtime subset of the four accepted CP2K 2026.2 exports.

Default: read-only preflight. --install writes exact /opt prefixes, followed by
the public module. --verify checks physical installed bytes without needing
the original SIFs. All copying is declarative; no container code is executed.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import time


PACKAGE = Path(__file__).resolve().parent
APP_ROOT = Path('/opt/apps/cp2k')
MODULE = Path('/opt/modules/modulefiles/apps/cp2k/2026.2-auto')
RECEIPT = 'share/sai/deployment.json'
POLICY = 'cp2k-2026.2-site-runtime-v1'
COMMANDS = {'cp2k.psmp', 'cp2k.popt', 'dumpdcd.psmp', 'graph.psmp', 'xyz2dcd.psmp'}


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def plain_path(path):
    """Check every existing path component without following symbolic links."""
    require(path.is_absolute() and '..' not in path.parts, 'noncanonical path: ' + str(path))
    for candidate in [*reversed(path.parents), path]:
        require(not candidate.is_symlink(), 'symbolic link in deployment path: ' + str(candidate))


def pinned_json(path, digest):
    plain_path(path)
    require(path.is_file() and path.stat().st_size <= 64 * 1024**2,
            'missing or oversized metadata: ' + str(path))
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == digest, 'metadata checksum mismatch: ' + str(path))
    return json.loads(raw)


def omission(relative):
    """Explicit build/test omissions; retain all unknown runtime data and libraries."""
    parts = PurePosixPath(relative).parts
    if len(parts) >= 2 and parts[0] == 'bin' and parts[1] not in COMMANDS:
        return 'unit test or miniapp'
    if parts[:1] == ('modulefiles',):
        return 'superseded by the four-partition site selector'
    if parts[:2] in {('lib64', 'cmake'), ('lib64', 'pkgconfig'),
                     ('lib', 'cmake'), ('lib', 'pkgconfig')} or parts[:3] == ('share', 'sai', 'cmake'):
        return 'CMake or development metadata'
    if relative in {'share/sai/cp2k_elpa_abi.f90', 'share/sai/elpa-consumer.log',
                    'share/sai/elpa-consumer-ldd.txt'}:
        return 'ELPA build-time consumer probe'
    if parts[:4] == ('dependencies', 'elpa', 'share', 'sai'):
        if parts[-1] not in {'libraries.sha256', 'source-archives.sha256',
                             'mpi-communicator-fix.json', 'version', 'target'}:
            return 'ELPA build diagnostic'
    if parts[:4] == ('dependencies', 'libxs', 'share', 'libxs') and parts[-1] != 'LICENSE.md':
        return 'LIBXS developer documentation'
    for index in range(1, len(parts) - 1):
        if parts[index - 1:index + 1] == ('lib', 'plumed'):
            if parts[index + 1] in {'patches', 'fortran', 'vim', 'modulefile',
                                    'plumed-patch', 'plumed-newcv', 'plumed-vim2html'}:
                return 'PLUMED integration, editor or Fortran development file'
            if parts[index + 1:] in [('scripts', 'patch.sh'), ('scripts', 'newcv.sh'),
                                     ('scripts', 'vim2html.sh')]:
                return 'PLUMED integration or editor helper'
    if parts[-1:] in {('USING_HDF5_CMake.txt',), ('RELEASE.txt',), ('libhdf5.settings',)}:
        return 'HDF5 build or release documentation'
    return None


def runtime_records(manifest, prefix):
    original, removed = {}, []
    for row in manifest['files']:
        path = PurePosixPath('/' + row['path'])
        require(path.is_relative_to(prefix), 'inventory escapes the installation prefix')
        relative = str(path.relative_to(prefix))
        require(relative == '.' or (not relative.startswith('/') and
                '..' not in PurePosixPath(relative).parts), 'unsafe inventory path')
        require(relative not in original, 'duplicate inventory path')
        record = {**row, 'path': relative}
        require(row['kind'] in {'file', 'directory', 'symlink'}, 'special file in runtime')
        require(type(row['mode']) is int and 0 <= row['mode'] <= 0o777 and
                not row['mode'] & 0o022, 'unsafe runtime permissions')
        original[relative] = record
    selected = {}
    for relative, row in original.items():
        if row['kind'] == 'directory':
            continue
        reason = omission(relative)
        if reason:
            removed.append({'path': relative, 'reason': reason})
        else:
            selected[relative] = row
    # Preserve the original data directories, including any empty directories.
    for relative, row in original.items():
        if row['kind'] == 'directory' and (relative == 'share/cp2k/data' or
                                          relative.startswith('share/cp2k/data/')):
            selected[relative] = row
    for relative in list(selected):
        for parent in [PurePosixPath('.'), *PurePosixPath(relative).parents]:
            name = str(parent)
            require(original.get(name, {}).get('kind') == 'directory', 'missing inventory directory')
            selected[name] = original[name]
    require(selected.get('bin/cp2k.psmp', {}).get('kind') == 'file', 'missing CP2K executable')
    require(selected.get('share/sai/native-module.tcl', {}).get('kind') == 'file',
            'missing native module fragment')
    require(any(name.startswith('share/cp2k/data/') for name in selected), 'missing CP2K data')
    # A retained internal link must still resolve after pruning. Handle .. only
    # after each link expansion; no symlink directories are traversed when copying.
    for name, row in selected.items():
        if row['kind'] != 'symlink':
            continue
        pending, resolved, links = (str(prefix / name)).split('/'), [], 0
        while pending:
            part = pending.pop(0)
            if part in {'', '.'}:
                continue
            if part == '..':
                if resolved:
                    resolved.pop()
                continue
            resolved.append(part)
            current = PurePosixPath('/' + '/'.join(resolved))
            if current.is_relative_to(prefix):
                item = selected.get(str(current.relative_to(prefix)))
                require(item is not None, 'pruning would break runtime link: ' + name)
                if item['kind'] == 'symlink':
                    links += 1
                    require(links <= 40, 'cyclic runtime link: ' + name)
                    resolved.pop()
                    target = item['target']
                    if target.startswith('/'):
                        resolved = []
                    pending = target.split('/') + pending
                elif pending:
                    require(item['kind'] == 'directory', 'runtime link traverses a file')
        target = PurePosixPath('/' + '/'.join(resolved))
        roots = [prefix, *map(PurePosixPath, manifest['entry']['external_roots'])]
        require(any(target.is_relative_to(root) for root in roots), 'runtime link escapes declared roots')
    return sorted(selected.values(), key=lambda row: row['path']), removed


def verify_tree(root, records, *, installed=False, receipt=None):
    plain_path(root)
    expected = {row['path']: row for row in records}
    actual, pending = set(), [root]
    while pending:
        path = pending.pop()
        relative = str(path.relative_to(root))
        info = path.lstat()
        if installed and relative == RECEIPT:
            require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o444,
                    'invalid installed deployment receipt')
            require(path.read_bytes() == canonical(receipt), 'installed deployment receipt changed')
            continue
        row = expected.get(relative)
        require(row is not None, 'unlisted installed file: ' + str(path))
        actual.add(relative)
        kind = ('symlink' if stat.S_ISLNK(info.st_mode) else 'directory'
                if stat.S_ISDIR(info.st_mode) else 'file' if stat.S_ISREG(info.st_mode) else 'special')
        require(kind == row['kind'], 'file type mismatch: ' + str(path))
        if kind == 'symlink':
            require(os.readlink(path) == row['target'], 'symbolic link mismatch: ' + str(path))
        else:
            require(stat.S_IMODE(info.st_mode) == row['mode'], 'permissions mismatch: ' + str(path))
            if kind == 'directory':
                pending.extend(path.iterdir())
            else:
                require(info.st_size == row['size'] and checksum(path) == row['sha256'],
                        'payload checksum mismatch: ' + str(path))
    require(actual == set(expected), 'missing installed files: ' + str(root))
    if installed:
        require((root / RECEIPT).is_file(), 'missing installed deployment receipt')


def copy_records(source, destination, records):
    """Copy only listed bytes, preserving links; never mutate the accepted export."""
    plain_path(source)
    require(not destination.exists() and not destination.is_symlink(), 'destination already exists')
    destination.mkdir(mode=0o755)
    for row in records:
        relative = row['path']
        src, dst = source / relative, destination / relative
        info = src.lstat()
        kind = row['kind']
        if kind == 'directory':
            require(stat.S_ISDIR(info.st_mode), 'source directory changed: ' + str(src))
            if relative != '.':
                dst.mkdir(mode=0o755)
        elif kind == 'symlink':
            require(stat.S_ISLNK(info.st_mode) and os.readlink(src) == row['target'],
                    'source symbolic link changed: ' + str(src))
            dst.symlink_to(row['target'])
        else:
            require(stat.S_ISREG(info.st_mode), 'source file changed: ' + str(src))
            with src.open('rb') as incoming, dst.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
            dst.chmod(row['mode'])
    for row in reversed(records):
        if row['kind'] == 'directory':
            (destination / row['path']).chmod(row['mode'])


def load_deliveries(project, *, hash_images=None):
    release = json.loads((PACKAGE / 'release.json').read_text())
    if hash_images is None:
        hash_images = release.get('hash_images', True)
    require(release['schema'] == 1 and {d['partition'] for d in release['deliveries']} ==
            {'4V100', '16V100', '8V100V0', 'DSPRHBM'} and len(release['deliveries']) == 4,
            'release must identify exactly four accepted partitions')
    deliveries = []
    for index, spec in enumerate(release['deliveries'], start=1):
        started = time.monotonic()
        progress = f"[{index}/{len(release['deliveries'])}] {spec['partition']}"
        print(progress + ': checking accepted source metadata...', flush=True)
        prefix = APP_ROOT / ('cp2k-release-' + spec['build_id']) / spec['partition']
        source = (project / 'containers/software/cp2k/release' / spec['build_id'] /
                  spec['partition'] / (spec['run_id'] + '.native'))
        manifest = pinned_json(source / 'share/sai/manifest.json', spec['manifest_sha256'])
        export = pinned_json(source / 'share/sai/export.json', spec['export_sha256'])
        identity = manifest['entry']['identity']
        require(identity['install_prefix'] == str(prefix) and identity['partition'] == spec['partition']
                and identity['source_sha'] == release['source_sha'] and identity['track'] == 'release'
                and identity['source_version'] == release['version'] and identity['software'] == 'cp2k',
                'accepted delivery identity mismatch')
        require(export['image_sha256'] == spec['image_sha256'] and
                export['manifest_sha256'] == spec['manifest_sha256'], 'accepted export identity mismatch')
        image = source.with_suffix('.sif')
        published = (pinned_json(source.with_suffix('.json'), spec['sidecar_sha256'])
                     if 'sidecar_sha256' in spec else json.loads(source.with_suffix('.json').read_text()))
        require(published.get('published') is True and published.get('verified') is True and
                published.get('cp2k_science', {}).get('verified') is True and
                published.get('identity') == identity and published.get('sha256') == spec['image_sha256'],
                'SIF is not the accepted published delivery')
        plain_path(image)
        image_elapsed = 0
        if hash_images:
            image_started = time.monotonic()
            print(f"{progress}: hashing SIF ({image.stat().st_size / 1024**2:.2f} MiB)...", flush=True)
            require(checksum(image) == spec['image_sha256'], 'accepted SIF checksum mismatch')
            image_elapsed = time.monotonic() - image_started
        rows, removed = runtime_records(manifest, prefix)
        count = sum(row['kind'] == 'file' for row in rows)
        size = sum(row.get('size', 0) for row in rows)
        runtime_started = time.monotonic()
        print(f"{progress}: hashing {count} runtime files ({size / 1024**2:.2f} MiB)...", flush=True)
        # Read selected source files only. The immutable manifest pins all bytes;
        # excluded unit tests and development files do not enter the deployment.
        for row in rows:
            path = source / row['path']
            plain_path(path.parent)
            info = path.lstat()
            if row['kind'] == 'file':
                require(stat.S_ISREG(info.st_mode) and info.st_size == row['size'] and
                        checksum(path) == row['sha256'], 'source runtime checksum mismatch: ' + str(path))
            elif row['kind'] == 'symlink':
                require(stat.S_ISLNK(info.st_mode) and os.readlink(path) == row['target'],
                        'source runtime link mismatch: ' + str(path))
            else:
                require(stat.S_ISDIR(info.st_mode), 'source runtime directory mismatch: ' + str(path))
        print(f"{progress}: source verified in {time.monotonic() - started:.1f}s "
              f"(SIF {image_elapsed:.1f}s, runtime {time.monotonic() - runtime_started:.1f}s)", flush=True)
        receipt = {'schema': 1, 'policy': POLICY, 'identity': identity,
                   'image_sha256': spec['image_sha256'],
                   'source_native_manifest_sha256': spec['manifest_sha256'],
                   'source_export_sha256': spec['export_sha256'],
                   'omitted_files': removed, 'files': rows,
                   'scope': 'physical byte integrity; native runtime checks are separate'}
        if 'receipt_sha256' in spec:
            require(receipt == pinned_json(PACKAGE / (spec['partition'] + '-receipt.json'),
                                           spec['receipt_sha256']), 'runtime selection changed')
        deliveries.append({'prefix': prefix, 'source': source, 'receipt': receipt})
    return deliveries


def physical_prefix(root, prefix):
    return root / prefix.relative_to('/')


def shared_dependencies(root=Path('/')):
    release = json.loads((PACKAGE / 'release.json').read_text())
    if 'shared_elpa_sha256' not in release:
        return
    lock = pinned_json(PACKAGE / 'shared-elpa.json', release['shared_elpa_sha256'])
    for variant, spec in lock['variants'].items():
        prefix = physical_prefix(root, Path(spec['prefix']))
        plain_path(prefix)
        for name, digest in spec['files'].items():
            path = prefix / name
            plain_path(path)
            require(path.is_file() and checksum(path) == digest,
                    'shared ELPA changed: ' + str(path))
        for name, destination in spec['symlinks'].items():
            path = prefix / name
            plain_path(path.parent)
            require(path.is_symlink() and os.readlink(path) == destination and path.exists()
                    and path.resolve().is_relative_to(prefix), 'shared ELPA link changed: ' + str(path))
        print('Shared ELPA verified: ' + variant, flush=True)


def module_state(root=Path('/')):
    release = json.loads((PACKAGE / 'release.json').read_text())
    desired = (PACKAGE / '2026.2-auto').read_bytes()
    if 'module_sha256' in release:
        require(hashlib.sha256(desired).hexdigest() == release['module_sha256'], 'package module changed')
    target = physical_prefix(root, MODULE)
    plain_path(target)
    if not target.exists():
        return 'absent', None
    require(target.is_file() and stat.S_IMODE(target.stat().st_mode) == 0o644,
            'invalid public module file: ' + str(target))
    current = target.read_bytes()
    if current == desired:
        return 'current', current
    previous = release.get('previous_module_sha256')
    require(previous and hashlib.sha256(current).hexdigest() == previous,
            'existing site module differs; review it before deployment: ' + str(target))
    backup = target.with_name('.' + target.name + '.before-' + previous[:16])
    plain_path(backup)
    if backup.exists():
        require(backup.is_file() and backup.read_bytes() == current,
                'previous module backup differs: ' + str(backup))
    return 'previous', current


def publish_module(root=Path('/')):
    state, previous = module_state(root)
    target = physical_prefix(root, MODULE)
    make_directories(target.parent)
    if state == 'current':
        return
    if state == 'previous':
        digest = hashlib.sha256(previous).hexdigest()
        backup = target.with_name('.' + target.name + '.before-' + digest[:16])
        if not backup.exists():
            # A hard link preserves the complete old file before atomic replacement.
            os.link(target, backup)
        require(backup.read_bytes() == previous, 'module backup changed')
        print('Previous module retained: ' + str(backup), flush=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.2026.2-auto-', dir=target.parent)
    temporary = Path(temporary)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write((PACKAGE / '2026.2-auto').read_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o644)
    require(module_state(root) == (state, previous), 'public module changed during installation')
    if state == 'absent':
        os.link(temporary, target)
        temporary.unlink()
    else:
        os.replace(temporary, target)


def preflight(deliveries, root=Path('/')):
    for delivery in deliveries:
        target = physical_prefix(root, delivery['prefix'])
        plain_path(target)
        if target.exists():
            verify_tree(target, delivery['receipt']['files'], installed=True, receipt=delivery['receipt'])
        rows = delivery['receipt']['files']
        count = sum(row['kind'] == 'file' for row in rows)
        size = sum(row.get('size', 0) for row in rows)
        print(f"{target.name}: {count} files, {size / 1024**2:.2f} MiB; "
              f"{'already installed and identical' if target.exists() else 'ready to install'}", flush=True)
        print('  ' + str(delivery['prefix']), flush=True)
    module_state(root)


def make_directories(path):
    plain_path(path)
    for directory in [*reversed(path.parents), path]:
        if not directory.exists():
            directory.mkdir(mode=0o755)
            directory.chmod(0o755)  # Independent of an administrator's private umask.
        require(directory.is_dir(), 'deployment ancestor is not a directory: ' + str(directory))
    plain_path(path)


def install(deliveries, root=Path('/')):
    preflight(deliveries, root)
    app_root = physical_prefix(root, APP_ROOT)
    make_directories(app_root)
    lock_path = app_root / '.cp2k-2026.2-deployment.lock'
    plain_path(lock_path)
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        preflight(deliveries, root)
        shared_dependencies(root)
        for delivery in deliveries:
            target = physical_prefix(root, delivery['prefix'])
            if target.exists():
                continue
            make_directories(target.parent)
            stage = target.with_name('.' + target.name + '.incoming-' + str(os.getpid()))
            print('Installing ' + str(target), flush=True)
            copy_records(delivery['source'], stage, delivery['receipt']['files'])
            receipt = stage / RECEIPT
            with receipt.open('xb') as stream:
                stream.write(canonical(delivery['receipt']))
            receipt.chmod(0o444)
            verify_tree(stage, delivery['receipt']['files'], installed=True, receipt=delivery['receipt'])
            require(not target.exists() and not target.is_symlink(), 'destination appeared during installation')
            stage.rename(target)
        # Publish only once every physical prefix has passed readback.
        for delivery in deliveries:
            require(physical_prefix(root, delivery['prefix']).is_dir(), 'installation is incomplete')
        shared_dependencies(root)
        publish_module(root)
        print('Published module: ' + str(MODULE), flush=True)


def verify_installed(root=Path('/')):
    release = json.loads((PACKAGE / 'release.json').read_text())
    for spec in release['deliveries']:
        prefix = APP_ROOT / ('cp2k-release-' + spec['build_id']) / spec['partition']
        target = physical_prefix(root, prefix)
        plain_path(target / RECEIPT)
        receipt = json.loads((target / RECEIPT).read_text())
        if 'receipt_sha256' in spec:
            require(receipt == pinned_json(PACKAGE / (spec['partition'] + '-receipt.json'),
                                           spec['receipt_sha256']), 'installed receipt differs from release')
        require(receipt['policy'] == POLICY and receipt['identity']['install_prefix'] == str(prefix)
                and receipt['image_sha256'] == spec['image_sha256'] and
                receipt['source_native_manifest_sha256'] == spec['manifest_sha256'],
                'installed delivery differs from the accepted release')
        verify_tree(target, receipt['files'], installed=True, receipt=receipt)
        print(spec['partition'] + ': installed bytes, links and permissions verified', flush=True)
    require(module_state(root)[0] == 'current', 'installed site module differs')
    shared_dependencies(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--check', action='store_true', help='read-only preflight (default)')
    action.add_argument('--install', action='store_true', help='install exact /opt prefixes and publish module')
    action.add_argument('--verify', action='store_true', help='verify installed bytes without SIF/source access')
    parser.add_argument('--project-root', type=Path, default=Path('/home/stardust/sai-hpc-software'))
    parser.add_argument('--report', type=Path, help='write a new JSON file listing installed and omitted files')
    parser.add_argument('--check-images', action='store_true', help='also hash original SIFs during preflight/install')
    args = parser.parse_args()
    if args.verify:
        verify_installed()
    else:
        shared_dependencies()
        deliveries = load_deliveries(args.project_root.absolute(), hash_images=True if args.check_images else None)
        if args.install:
            install(deliveries)
        else:
            preflight(deliveries)
            print('Preflight passed. Install with: bash ' + str(PACKAGE / 'install.sh') + ' --install')
        if args.report:
            with args.report.open('xb') as stream:
                stream.write(canonical({'schema': 1, 'deliveries': [d['receipt'] for d in deliveries]}))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit('Deployment stopped: ' + str(error)) from error
