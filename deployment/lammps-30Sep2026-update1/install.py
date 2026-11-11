#!/usr/bin/env python3
"""Install the accepted LAMMPS update1 SIF payload; reuse installed DeePMD."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

from runtime_files import (digest, directories, pinned, plain, require,
                           runtime_records, verify_records)

PACKAGE = Path(__file__).resolve().parent
MODULE = Path('/opt/modules/modulefiles/apps/lammps/30Sep2026_update1-deepmd320-auto')
SOURCE = '098ffdefa525cabc82272925de02e9884f58c657'
BUILD = 'stable_30Sep2026_update1-g098ffdefa525-rb6a6aadddb8b'
PARTITIONS = {'4V100', '16V100', '8V100V0'}


def relative(name):
    path = Path(name)
    require(name and not path.is_absolute() and '..' not in path.parts and str(path) == name,
            'unsafe package path: ' + name)
    return path


def load_release():
    release = json.loads((PACKAGE / 'release.json').read_text())
    require(release['schema'] == 1 and release['version'] == 'stable_30Sep2026_update1', 'wrong release')
    require(len(release['deliveries']) == 3
            and {r['partition'] for r in release['deliveries']} == PARTITIONS, 'incomplete partition matrix')
    require(digest(PACKAGE / MODULE.name) == release['module_sha256'], 'module changed')
    for name, checksum in release['build_info'].items():
        path = PACKAGE / relative(name)
        plain(path)
        require(digest(path) == checksum and path.stat().st_mode & stat.S_IROTH,
                'missing or changed static build record')
    for row in release['deliveries']:
        identity = row['identity']
        require(identity['software'] == 'lammps' and identity['track'] == 'release'
                and identity['source_sha'] == SOURCE and identity['build_id'] == BUILD
                and identity['partition'] == row['partition']
                and identity['source_ref'] == identity['source_version'] == release['version'], 'wrong identity')
        require(row['prefix'] == identity['install_prefix'] ==
                '/opt/apps/lammps/lammps-release-' + BUILD + '/' + row['partition'], 'wrong prefix')
        for key in ('manifest', 'acceptance', 'verification', 'image', 'dependency_records'):
            relative(row[key])
        manifest = pinned(PACKAGE / row['manifest'], row['manifest_sha256'])
        files, omitted = runtime_records(manifest, row)
        require(files == row['files'] and omitted == row['omitted'], 'runtime selection changed')
        require(manifest['entry']['site_dependencies'] == [row['dependency']], 'dependency changed')
        dep = row['dependency']['identity']
        require(dep['software'] == 'deepmd-kit' and dep['source_version'] == 'v3.2.0'
                and dep['partition'] == row['partition'], 'wrong installed DeePMD')
    return release


def module_text(root=Path('/')):
    text = (PACKAGE / MODULE.name).read_text()
    if root != Path('/'):
        old = '/opt/apps/lammps/lammps-release-' + BUILD
        text = text.replace(old, str(root / old.lstrip('/')))
    return text


def preflight(release, root=Path('/'), *, installed=False):
    plain(root)
    for row in release['deliveries']:
        part = row['partition']
        print(part + ': verifying ' + ('native files' if installed else 'accepted SIF'), flush=True)
        proof = pinned(PACKAGE / row['verification'], row['verification_sha256'])
        accepted = pinned(PACKAGE / row['acceptance'], row['acceptance_sha256'])
        require(proof['passed'] is True and proof['scientific_verified'] is True
                and proof['accounting'][1:4] == ['COMPLETED', '0:0', part]
                and proof['acceptance_sha256'] == row['acceptance_sha256']
                and proof['artifact_sha256'] == accepted['artifact_sha256'] == row['image_sha256']
                and proof['delivery'] == accepted['delivery'] ==
                dict(schema=1, identity=row['identity'], deepmd_dependency=row['dependency']),
                'scientific evidence does not belong to this SIF')
        require(accepted['passed'] is True and len(accepted['samples']) == 14,
                'incomplete accepted GPU case matrix')
        dep = pinned(PACKAGE / row['dependency_records'], row['dependency_records_sha256'])
        require(dep['identity'] == row['dependency']['identity'], 'wrong installed dependency inventory')
        verify_records(Path(dep['identity']['install_prefix']), {r['path']: r for r in dep['files']})
        if not installed:
            image = PACKAGE / row['image']
            plain(image)
            require(image.is_file() and image.stat().st_size == row['image_bytes']
                    and digest(image) == row['image_sha256'], 'SIF changed')
        target = root / row['prefix'].lstrip('/')
        plain(target)
        if installed or target.exists():
            verify_records(target, {r['path']: r for r in row['files']})
        print(f"  {sum(r['kind'] == 'file' for r in row['files'])} files; "
              f"{sum(r.get('size', 0) for r in row['files']) / 1024**2:.2f} MiB", flush=True)
    module = root / MODULE.relative_to('/')
    plain(module)
    if installed or module.exists():
        require(module.is_file() and stat.S_IMODE(module.stat().st_mode) == 0o644
                and module.read_text() == module_text(root), 'existing module differs')


def image_offset(image):
    listing = subprocess.check_output(['/opt/devtools/apptainer/1.4.4/bin/apptainer',
                                       'sif', 'list', str(image)], text=True)
    partitions = re.findall(r'^\s*\d+\s*\|[^\n|]*\|[^\n|]*\|\s*(\d+)-(\d+)\s*\|\s*FS \(Squashfs/\*System/[^)]+\)\s*$',
                            listing, re.MULTILINE)
    require(len(partitions) == 1, 'expected one primary Squashfs partition')
    start, end = map(int, partitions[0])
    require(0 <= start < end <= image.stat().st_size, 'invalid SIF partition boundaries')
    return start


def extract(row, destination):
    files = row['files']
    for item in files:
        if item['kind'] == 'directory':
            directories(destination / item['path'])
    regular = [r for r in files if r['kind'] == 'file']
    image = PACKAGE / row['image']
    command = ['unsquashfs', '-processors', '2', '-no-progress', '-no-wildcards',
               '-o', str(image_offset(image)), '-cat', str(image),
               *[row['prefix'].lstrip('/') + '/' + r['path'] for r in regular]]
    with subprocess.Popen(command, stdout=subprocess.PIPE) as process:
        try:
            for record in regular:
                path = destination / record['path']
                remaining, checksum = record['size'], hashlib.sha256()
                with path.open('xb') as stream:
                    while remaining:
                        block = process.stdout.read(min(remaining, 1024**2))
                        require(block, 'truncated SIF file: ' + record['path'])
                        stream.write(block); checksum.update(block); remaining -= len(block)
                require(checksum.hexdigest() == record['sha256'], 'SIF payload differs: ' + record['path'])
                path.chmod(record['mode'])
            require(not process.stdout.read(1) and process.wait() == 0, 'unexpected extraction output')
        except BaseException:
            process.kill()
            raise
    for record in files:
        if record['kind'] == 'symlink':
            (destination / record['path']).symlink_to(record['target'])
    for record in reversed(files):
        if record['kind'] == 'directory':
            (destination / record['path']).chmod(record['mode'])
    verify_records(destination, {r['path']: r for r in files})


def install(release, root=Path('/')):
    preflight(release, root)
    app_root = root / 'opt/apps/lammps'
    directories(app_root)
    descriptor = os.open(app_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        for row in release['deliveries']:
            target = root / row['prefix'].lstrip('/')
            plain(target)
            if target.exists():
                verify_records(target, {r['path']: r for r in row['files']})
                continue
            directories(target.parent)
            stage = target.with_name('.' + target.name + '.incoming-' + str(os.getpid()))
            require(not stage.exists() and not stage.is_symlink(), 'staging directory already exists')
            stage.mkdir(mode=0o755)
            extract(row, stage)
            require(not target.exists() and not target.is_symlink(), 'destination appeared while copying')
            stage.rename(target)
            print('Installed ' + str(target), flush=True)
        module = root / MODULE.relative_to('/')
        directories(module.parent)
        if not module.exists():
            stage = module.with_name('.' + module.name + '.incoming-' + str(os.getpid()))
            with stage.open('x') as stream:
                stream.write(module_text(root)); stream.flush(); os.fsync(stream.fileno())
            stage.chmod(0o644)
            os.link(stage, module)
            stage.unlink()
        preflight(release, root, installed=True)
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    operations = parser.add_mutually_exclusive_group()
    for name in ('check', 'install', 'verify'):
        operations.add_argument('--' + name, action='store_true')
    parser.add_argument('--stage-root', type=Path, help='trial installation below project storage')
    args = parser.parse_args()
    root = args.stage_root or Path('/')
    if args.stage_root:
        plain(root)
        require(root.is_relative_to('/org/org-open-sai/proj-sai-hpc-software/operations'),
                'trial root must be in project operations storage')
    release = load_release()
    if args.install:
        install(release, root)
    else:
        preflight(release, root, installed=args.verify)
    print('LAMMPS_UPDATE1_' + ('INSTALLED' if args.install else 'VERIFIED' if args.verify else 'PREFLIGHT_PASSED'))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit('Installation stopped: ' + str(error)) from error
