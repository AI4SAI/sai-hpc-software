#!/usr/bin/env python3
"""Prepare a checksum-locked ext3 bootstrap overlay on the connected workstation.

Only transfer the image and receipt to SAI, never its expanded source tree.
No compiler is executed and no architecture-specific dependencies are built.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import zipfile

from cp2k_spack import SPACK_SHA256, PACKAGES_SHA256, SPACK_COMMIT, PACKAGES_COMMIT
from cp2k_spack_native import WHEELS


def checksum(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def manifest():
    return {'schema': 1, 'purpose': 'source-and-offline-solver-only',
            'spack_commit': SPACK_COMMIT, 'packages_commit': PACKAGES_COMMIT,
            'spack_sha256': SPACK_SHA256, 'packages_sha256': PACKAGES_SHA256,
            'solver_wheels': WHEELS, 'compiled_dependencies': False}


def validate_sources(archives, wheels):
    expected = {archives / 'spack.tar.gz': SPACK_SHA256,
                archives / 'packages.tar.gz': PACKAGES_SHA256}
    expected.update({wheels / name: digest for name, digest in WHEELS.items()})
    for path, digest in expected.items():
        if path.is_symlink() or checksum(path) != digest:
            raise ValueError('bootstrap input checksum mismatch: ' + str(path))


def unpack_archive(archive, destination, temporary):
    temporary.mkdir()
    with tarfile.open(archive, 'r:gz') as stream:
        stream.extractall(temporary, filter='data')
    roots = list(temporary.iterdir())
    if len(roots) != 1 or not roots[0].is_dir() or roots[0].is_symlink():
        raise ValueError('expected one pinned upstream archive root')
    roots[0].rename(destination)


def build(archives, wheels, output):
    archives, wheels, output = Path(archives), Path(wheels), Path(output)
    # This tool expands source on the connected workstation only.
    if output.is_relative_to('/home/stardust/sai-hpc-software'):
        raise ValueError('prepare bootstrap images on the workstation, not SAI')
    validate_sources(archives, wheels)
    if output.exists() or output.is_symlink() or output.with_suffix('.json').exists():
        raise ValueError('refusing to replace a bootstrap image or receipt')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cp2k-spack-seed-', dir=output.parent) as temporary:
        stage = Path(temporary)
        rootfs = stage / 'rootfs'
        workspace = rootfs / 'upper/workspace'
        workspace.mkdir(parents=True)
        (rootfs / 'work').mkdir()
        for archive, name in [('spack.tar.gz', 'spack'), ('packages.tar.gz', 'spack-packages')]:
            unpack_archive(archives / archive, workspace / name, stage / name)
        solver = workspace / 'solver'
        solver.mkdir()
        for name in WHEELS:
            with zipfile.ZipFile(wheels / name) as stream:
                if any(Path(p).is_absolute() or '..' in Path(p).parts for p in stream.namelist()):
                    raise ValueError('unsafe solver wheel path')
                stream.extractall(solver)
        (workspace / 'spack-bootstrap.json').write_text(json.dumps(manifest(), sort_keys=True) + '\n')
        with output.open('xb') as stream:
            stream.truncate(4096 * 1024 * 1024)
        subprocess.run(['/usr/sbin/mke2fs', '-q', '-t', 'ext3', '-F', '-b', '4096',
                        '-m', '0', '-E', 'root_owner=0:0', '-d', str(rootfs), str(output)], check=True)
        # Modify ownership in this newly generated, unmounted image only.
        # Do not chown real host files or rely on LD_PRELOAD/fakeroot support.
        commands = stage / 'ownership.debugfs'
        with commands.open('x') as stream:
            for entry in rootfs.rglob('*'):
                path = '/' + str(entry.relative_to(rootfs))
                if any(char in path for char in '\n\r"\\'):
                    raise ValueError('unsupported image path for ownership normalization')
                for field in ('uid', 'gid'):
                    stream.write(f'set_inode_field "{path}" {field} 0\n')
        with (stage / 'ownership.log').open('w') as log:
            subprocess.run(['debugfs', '-w', '-f', str(commands), str(output)],
                           stdout=log, stderr=log, check=True)
        for path in ('/upper', '/work', '/upper/workspace', '/upper/workspace/spack/bin/spack'):
            info = subprocess.check_output(['debugfs', '-R', 'stat ' + path, str(output)],
                                           text=True, stderr=subprocess.STDOUT)
            if not re.search(r'User:\s+0\s+Group:\s+0\b', info):
                raise ValueError('image ownership normalization failed: ' + info)
    subprocess.run(['e2fsck', '-fn', str(output)], check=True)
    digest = checksum(output)
    receipt = {**manifest(), 'overlay_sha256': digest, 'overlay_bytes': output.stat().st_size}
    with output.with_suffix('.json').open('x') as stream:
        json.dump(receipt, stream, indent=2, sort_keys=True)
        stream.write('\n')
    output.chmod(0o444)
    print(json.dumps({'overlay': str(output), 'sha256': digest}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archives', type=Path)
    parser.add_argument('wheels', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    build(args.archives, args.wheels, args.output)
