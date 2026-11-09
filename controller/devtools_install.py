#!/usr/bin/env python3
"""Administrator-only installation of inventoried, immutable devtools payloads."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys

LIBRARIES = {'libint', 'libxsmm', 'libvori', 'hdf5', 'plumed', 'cosma', 'spla', 'costa', 'tiled-mm', 'gcc-runtime'}
PARTITIONS = {'16V100', '4V100', '8V100V0', 'DSPRHBM'}
APPTAINER = '/opt/devtools/apptainer/1.4.4/bin/apptainer'
APP_LIBRARY_PREFIXES = {
    'qe-hdf5': '/opt/apps/qe/dependencies/hdf5/1.14.6-nvhpc26.3-ompi5.0.10-f90/DSPRHBM',
    'abacus-libtorch': '/opt/apps/abacus/dependencies/libtorch/2.1.2-cpu-cxx11abi1',
}


def require(value, message):
    if not value:
        raise ValueError(message)


def checksum(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def plain(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'Noncanonical path: ' + str(path))
    for parent in (*reversed(path.parents), path):
        require(not parent.is_symlink(), 'Symlink in path: ' + str(parent))
    return path


def inventory(root):
    root = plain(root)
    require(root.is_dir(), 'Missing installation: ' + str(root))
    require(not root.stat().st_mode & 0o7022, 'Unsafe installation root permissions: ' + str(root))
    rows = []
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(parent) / name
            info = path.lstat()
            row = {'path': str(path.relative_to(root)), 'mode': stat.S_IMODE(info.st_mode)}
            if stat.S_ISLNK(info.st_mode):
                link = os.readlink(path)
                resolved = path.resolve(strict=True)
                internal = not Path(link).is_absolute() and resolved.is_relative_to(root)
                # Spack's gcc-runtime consists of links to the external GCC.
                # Keep those links and pin their actual file contents as well.
                external = resolved.is_relative_to('/opt/devtools/gcc/14.3.0')
                require(internal or (external and resolved.is_file()), 'External or dangling symlink: ' + str(path))
                row.update(kind='symlink', target=link)
                if external:
                    row['external_sha256'] = checksum(resolved)
            else:
                require(not row['mode'] & 0o7022, 'Unsafe permissions: ' + str(path))
                if stat.S_ISDIR(info.st_mode):
                    row.update(kind='directory')
                elif stat.S_ISREG(info.st_mode):
                    row.update(kind='file', size=info.st_size, sha256=checksum(path))
                else:
                    raise ValueError('Special file in payload: ' + str(path))
            rows.append(row)
    return sorted(rows, key=lambda row: row['path'])


def verify(root, expected):
    observed = inventory(root)
    if observed != expected:
        before = {row['path']: row for row in expected}
        after = {row['path']: row for row in observed}
        changed = sorted(name for name in before.keys() | after.keys()
                         if before.get(name) != after.get(name))
        detail = {name: {'expected': before.get(name), 'observed': after.get(name)}
                  for name in changed[:8]}
        raise ValueError('Installation differs from manifest: ' + str(root)
                         + '; differences=' + json.dumps(detail, sort_keys=True)
                         + '; changed_paths=' + str(len(changed)))


def restore_extracted_modes(root, expected):
    """Restore SIF modes on a new extraction affected by inherited host ACLs."""
    root = plain(root)
    root.chmod(0o755)
    for row in expected:
        if row['kind'] == 'symlink':
            continue
        relative = PurePosixPath(row['path'])
        require(relative.parts and not relative.is_absolute() and '..' not in relative.parts,
                'Unsafe inventory path')
        mode = row['mode']
        require(isinstance(mode, int) and 0 <= mode <= 0o777 and not mode & 0o7022,
                'Unsafe extraction permissions')
        path = plain(root / relative)
        require((row['kind'] == 'file' and path.is_file()) or
                (row['kind'] == 'directory' and path.is_dir()), 'Extraction file type changed')
        path.chmod(mode)


def package_prefix(record):
    name = record['name']
    if name in APP_LIBRARY_PREFIXES:
        require(record['prefix'] == APP_LIBRARY_PREFIXES[name], 'Unexpected application dependency prefix')
        return plain(Path(record['prefix']))
    require(name in LIBRARIES, 'Not an approved dependency library: ' + name)
    pattern = (r'/opt/devtools/' + re.escape(name) + r'/[A-Za-z0-9][A-Za-z0-9_.-]*'
               r'-gcc14\.3\.0-[a-z0-9_.-]+-[a-z2-7]{32}(?:-p[0-9a-f]{12})?')
    prefix = record['prefix']
    require(bool(re.fullmatch(pattern, prefix)), 'Unexpected library prefix: ' + prefix)
    require(len(PurePosixPath(prefix).parts) == 5, 'Unexpected prefix depth')
    return plain(Path(prefix))


def read_release(package):
    package = plain(package)
    manifest = plain(package / 'release.json')
    receipt = plain(package / 'release.sha256')
    require(checksum(manifest) == receipt.read_text().strip(), 'Release checksum mismatch')
    release = json.loads(manifest.read_text())
    require(release.get('schema') == 1 and release.get('purpose') in
            ('cp2k-spack-devtools', 'application-dependencies'), 'Wrong release manifest')
    require(set(release['partitions']) <= PARTITIONS and release['partitions'], 'Invalid partitions')
    records = release['packages']
    require(records and len({row['prefix'] for row in records}) == len(records), 'Duplicate or empty package set')
    allowed = LIBRARIES if release['purpose'] == 'cp2k-spack-devtools' else set(APP_LIBRARY_PREFIXES)
    require(all(row['name'] in allowed for row in records), 'Library does not belong to this release family')
    for record in records:
        package_prefix(record)
        require(('payload' in record) != ('image' in record), 'Expected one payload source')
        for key in (('payload' if 'payload' in record else 'image'), 'manifest'):
            relative = PurePosixPath(record[key])
            require(not relative.is_absolute() and '..' not in relative.parts and str(relative) == record[key],
                    'Unsafe handoff path')
            plain(package / relative)
        source = package / record['manifest']
        require(checksum(source) == record['manifest_sha256'], 'File manifest checksum mismatch')
        rows = json.loads(source.read_text())
        for row in rows:
            relative = PurePosixPath(row['path'])
            require(not relative.is_absolute() and '..' not in relative.parts, 'Unsafe inventory path')
        record['inventory'] = rows
    return release


def sif_offset(image):
    # Same primary-partition boundary rule as export_native.SquashfsReader.
    # This reads the SIF header; it never executes a container or payload.
    listing = subprocess.check_output([APPTAINER, 'sif', 'list', str(image)], text=True)
    partitions = re.findall(r'^\s*\d+\s*\|[^\n|]*\|[^\n|]*\|\s*(\d+)-(\d+)\s*\|\s*FS \(Squashfs/\*System/[^)]+\)\s*$',
                            listing, re.MULTILINE)
    require(len(partitions) == 1, 'Expected one primary Squashfs partition')
    start, end = map(int, partitions[0])
    require(0 <= start < end <= image.stat().st_size, 'Invalid SIF partition boundaries')
    return start


def extract_package(image, prefix, scratch, expected):
    offset = sif_offset(image)
    subprocess.run(['unsquashfs', '-no-progress', '-no-xattrs', '-no-wildcards', '-processors', '2',
                    '-o', str(offset), '-d', str(scratch), str(image), str(prefix).lstrip('/')], check=True)
    source = scratch / prefix.relative_to('/')
    restore_extracted_modes(source, expected)
    verify(source, expected)
    return source


def deploy(package, *, install=False, installed=False):
    package = plain(package)
    release = read_release(package)
    checked_images = {}
    # Check every source and existing target before starting any copy.
    for row in release['packages']:
        prefix = package_prefix(row)
        if not installed:
            if 'payload' in row:
                verify(package / row['payload'], row['inventory'])
            else:
                image = plain(package / row['image'])
                if image not in checked_images:
                    checked_images[image] = checksum(image)
                require(checked_images[image] == row['image_sha256'], 'Library SIF checksum mismatch')
        if installed or prefix.exists():
            verify(prefix, row['inventory'])
        size = sum(item.get('size', 0) for item in row['inventory'])
        print(f"{row['name']}: {size / 1024**2:.1f} MiB; {prefix}", flush=True)
    if install:
        for row in release['packages']:
            prefix = package_prefix(row)
            if prefix.exists():
                continue
            prefix.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            plain(prefix.parent)
            stage = prefix.with_name('.' + prefix.name + f'.install-{os.getpid()}')
            require(not stage.exists() and not stage.is_symlink(), 'Staging path already exists')
            if 'payload' in row:
                shutil.copytree(package / row['payload'], stage, symlinks=True)
                stage.chmod(0o755)
                verify(stage, row['inventory'])
                source = stage
            else:
                source = extract_package(package / row['image'], prefix, stage, row['inventory'])
            require(not prefix.exists() and not prefix.is_symlink(), 'Target appeared during copy')
            source.rename(prefix)
            if source != stage:
                shutil.rmtree(stage)  # Only this invocation's empty extraction parents.
        print('Installed libraries. Run --verify, then compute-node application acceptance before activating reuse.')
    elif installed:
        print('Physical library inventories verified; this does not replace compute-node acceptance.')
    else:
        print('Read-only preflight passed. Administrator: bash install.sh --install')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--check', action='store_true')
    modes.add_argument('--install', action='store_true')
    modes.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    deploy(Path(__file__).resolve().parent, install=args.install, installed=args.verify)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        sys.exit('Installation stopped: ' + str(error))
