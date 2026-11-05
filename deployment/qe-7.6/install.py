#!/usr/bin/env python3
"""Install accepted QE 7.6 runtime files and its partition-selecting module.

Default is read-only preflight. Installation verifies all sources before copying,
preserves existing different installations, and publishes the module last.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

PACKAGE = Path(__file__).resolve().parent
PROJECT = PACKAGE
MODULE = Path('/opt/modules/modulefiles/apps/qe/7.6-auto')
SOURCE = '9f93ddec427d2b9a45bb72d828c6d324f62fcabd'
PARTITIONS = {'DSPRHBM', '16V100', '4V100', '8V100V0'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def plain(path):
    require(path.is_absolute() and '..' not in path.parts, 'noncanonical path: ' + str(path))
    for part in [*reversed(path.parents), path]:
        require(not part.is_symlink(), 'symlink in deployment path: ' + str(part))


def relative(value):
    path = Path(value)
    require(value and not path.is_absolute() and '..' not in path.parts
            and str(path) == value and value != '.', 'unsafe relative path: ' + str(value))
    return path


def pinned(path, sha):
    plain(path)
    require(path.is_file() and path.stat().st_size < 64 * 1024**2, 'missing/oversized metadata: ' + str(path))
    require(digest(path) == sha, 'metadata changed: ' + str(path))
    return json.loads(path.read_text())


def runtime_records(manifest, source):
    """QE and HDF5 are static; retain all application commands and licenses."""
    prefix = Path(manifest['entry']['identity']['install_prefix'])
    commands = manifest['entry']['commands']
    require(len(commands) == 67 and all(value == 'bin/' + name for name, value in commands.items()),
            'unexpected QE command suite')
    records = []
    for item in manifest['files']:
        path = Path('/' + item['path'])
        require(path.is_relative_to(prefix) and '..' not in path.parts, 'inventory escapes prefix')
        name = str(path.relative_to(prefix))
        if item['kind'] == 'directory':
            continue
        if name.startswith(('bin/', 'share/licenses/')):
            require(item['kind'] == 'file', 'unexpected runtime link or special file')
            require(type(item['mode']) is int and 0 <= item['mode'] <= 0o777
                    and not item['mode'] & 0o022, 'unsafe runtime mode')
            records.append(dict(path=name, source=str(Path(source) / name),
                                sha256=item['sha256'], size=item['size'], mode=item['mode']))
    require({row['path'] for row in records if row['path'].startswith('bin/')} == set(commands.values()),
            'runtime inventory does not contain every QE executable')
    return sorted(records, key=lambda row: row['path'])


def load_release():
    release = json.loads((PACKAGE / 'release.json').read_text())
    require(release['schema'] == 1 and release['software'] == 'qe' and release['version'] == 'qe-7.6',
            'wrong release')
    require(len(release['deliveries']) == 4
            and {row['partition'] for row in release['deliveries']} == PARTITIONS, 'expected four partitions')
    require(release['module_sha256'] == digest(PACKAGE / MODULE.name), 'public module changed')
    for row in release['deliveries']:
        identity = row['identity']
        require(identity['software'] == 'qe' and identity['track'] == 'release'
                and identity['source_sha'] == SOURCE and identity['source_version'] == 'qe-7.6'
                and identity['partition'] == row['partition'], 'wrong source/partition identity')
        require(row['prefix'] == identity['install_prefix'] ==
                '/opt/apps/qe/qe-release-' + identity['build_id'] + '/' + row['partition'], 'wrong native prefix')
        for key in ('manifest', 'publication', 'image'):
            relative(row[key])
        names = [record['path'] for record in row['files']]
        require(len(names) == len(set(names)), 'duplicate runtime path')
        for record in row['files']:
            relative(record['source']); relative(record['path'])
            require(record['path'].startswith(('bin/', 'share/licenses/')), 'non-runtime payload')
            require(type(record['mode']) is int and 0 <= record['mode'] <= 0o777
                    and not record['mode'] & 0o022, 'unsafe installed mode')
    relative(release['hdf5_license']['source'])
    require(release['hdf5_license']['image_partition'] == 'DSPRHBM', 'wrong HDF5 license source')
    return release


def verify_file(path, record):
    plain(path)
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_size == record['size']
            and digest(path) == record['sha256'], 'payload changed: ' + str(path))
    require(stat.S_IMODE(info.st_mode) == record['mode'], 'permissions changed: ' + str(path))


def verify_installed(root, files):
    plain(root)
    expected = {record['path']: record for record in files}
    directories = {'.'} | {str(parent) for name in expected for parent in Path(name).parents}
    pending, seen = [root], set()
    while pending:
        path = pending.pop()
        plain(path)
        name = str(path.relative_to(root))
        if path.is_dir():
            require(name in directories and stat.S_IMODE(path.stat().st_mode) == 0o755,
                    'unexpected installed directory: ' + str(path))
            pending.extend(path.iterdir())
        else:
            require(name in expected, 'unlisted installed file: ' + str(path))
            verify_file(path, expected[name])
        seen.add(name)
    require(seen == set(expected) | directories, 'incomplete installed tree: ' + str(root))


def preflight(release, project, physical_root=Path('/'), *, installed=False):
    plain(project); plain(physical_root)
    for row in release['deliveries']:
        print(row['partition'] + ': checking ' + ('installed files' if installed else 'accepted export'), flush=True)
        if not installed:
            publication = pinned(project / row['publication'], row['publication_sha256'])
            require(all(publication.get(key) is True for key in ('published', 'build_verified', 'verified')),
                    'release has not passed acceptance')
            science = publication['qe_science']
            require(science['verified'] is True and science['artifact_sha256'] == row['image_sha256']
                    and science['identity'] == row['identity'], 'scientific proof differs from this image')
            if row['partition'] != 'DSPRHBM':
                require(science['checks']['gpu_executed'] is True, 'missing GPU execution proof')
            image = project / row['image']
            plain(image)
            require(row['origin_image'] == publication['artifact'] and row['image_sha256'] == publication['sha256']
                    == digest(image), 'SIF changed')
            export = publication['native_export']
            require(export['roundtrip_verified'] is True and export['image_sha256'] == row['image_sha256']
                    and publication['identity'] == row['identity'], 'native export is not accepted')
            receipt, = export['receipt']['installations']
            require(row['manifest_sha256'] == receipt['manifest_sha256'], 'export provenance changed')
            manifest = pinned(project / row['manifest'], row['manifest_sha256'])
            require(manifest['entry']['identity'] == row['identity'], 'native identity changed')
            expected = runtime_records(manifest, row['prefix'].lstrip('/')) + [release['hdf5_license']]
            require(sorted(expected, key=lambda item: item['path']) == row['files'], 'runtime selection changed')
            if row['partition'] == 'DSPRHBM':
                license = release['hdf5_license']
                original, = [record for record in manifest['files'] if record['path'] == license['source']]
                require(original['kind'] == 'file' and original['sha256'] == license['sha256']
                        and original['size'] == license['size'], 'HDF5 license differs from accepted image')
        target = physical_root / Path(row['prefix']).relative_to('/')
        plain(target)
        if installed or target.exists():
            verify_installed(target, row['files'])
        print(f"  {len(row['files'])} files, {sum(r['size'] for r in row['files']) / 1024**2:.2f} MiB", flush=True)
    module = physical_root / MODULE.relative_to('/')
    plain(module)
    if installed or module.exists():
        require(module.is_file() and stat.S_IMODE(module.stat().st_mode) == 0o644
                and module.read_bytes() == (PACKAGE / MODULE.name).read_bytes(), 'existing public module differs')


def directories(path):
    plain(path)
    for parent in [*reversed(path.parents), path]:
        if not parent.exists():
            parent.mkdir(mode=0o755)
            parent.chmod(0o755)
        require(parent.is_dir(), 'installation ancestor is not a directory')


def image_offset(image):
    listing = subprocess.check_output(['/opt/devtools/apptainer/1.4.4/bin/apptainer',
                                       'sif', 'list', str(image)], text=True)
    partitions = re.findall(r'^\s*\d+\s*\|[^\n|]*\|[^\n|]*\|\s*(\d+)-(\d+)\s*\|\s*FS \(Squashfs/\*System/[^)]+\)\s*$',
                            listing, re.MULTILINE)
    require(len(partitions) == 1, 'expected one primary Squashfs partition')
    start, end = map(int, partitions[0])
    require(0 <= start < end <= image.stat().st_size, 'invalid SIF partition boundaries')
    return start


def extract_files(image, files, destination):
    # Only read file contents. unsquashfs never creates paths or executes code.
    command = ['unsquashfs', '-processors', '2', '-no-progress', '-no-wildcards',
               '-o', str(image_offset(image)), '-cat', str(image), *[row['source'] for row in files]]
    with subprocess.Popen(command, stdout=subprocess.PIPE) as process:
        try:
            for record in files:
                path = destination / record['path']
                directories(path.parent)
                remaining, checksum = record['size'], hashlib.sha256()
                with path.open('xb') as stream:
                    while remaining:
                        block = process.stdout.read(min(remaining, 1024 * 1024))
                        require(block, 'truncated SIF payload: ' + record['path'])
                        stream.write(block); checksum.update(block); remaining -= len(block)
                require(checksum.hexdigest() == record['sha256'], 'SIF file changed: ' + record['path'])
                path.chmod(record['mode'])
            require(not process.stdout.read(1) and process.wait() == 0, 'unexpected SIF extraction result')
        except BaseException:
            process.kill()
            raise


def install(release, project, physical_root=Path('/')):
    preflight(release, project, physical_root)
    app_root = physical_root / 'opt/apps/qe'
    directories(app_root)
    descriptor = os.open(app_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        for row in release['deliveries']:
            target = physical_root / Path(row['prefix']).relative_to('/')
            plain(target)
            if target.exists():
                verify_installed(target, row['files'])
                continue
            directories(target.parent)
            stage = target.with_name('.' + target.name + '.incoming-' + str(os.getpid()))
            require(not stage.exists() and not stage.is_symlink(), 'staging path already exists')
            stage.mkdir(mode=0o755)
            stage.chmod(0o755)
            images = {item['partition']: project / item['image'] for item in release['deliveries']}
            for partition in images:
                files = [record for record in row['files']
                         if record.get('image_partition', row['partition']) == partition]
                if files:
                    extract_files(images[partition], files, stage)
            verify_installed(stage, row['files'])
            require(not target.exists() and not target.is_symlink(), 'destination appeared while copying')
            stage.rename(target)
            print('Installed ' + str(target), flush=True)
        module = physical_root / MODULE.relative_to('/')
        directories(module.parent)
        if not module.exists():
            stage = module.with_name('.' + module.name + '.incoming-' + str(os.getpid()))
            with stage.open('xb') as stream:
                stream.write((PACKAGE / MODULE.name).read_bytes())
                stream.flush(); os.fsync(stream.fileno())
            stage.chmod(0o644)
            os.link(stage, module)
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
    args = parser.parse_args()
    release = load_release()
    if args.install:
        install(release, PROJECT)
    else:
        preflight(release, PROJECT, installed=args.verify)
    print('QE_NATIVE_' + ('INSTALLED' if args.install else 'VERIFIED' if args.verify else 'PREFLIGHT_PASSED'))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit('Installation stopped: ' + str(error)) from error
