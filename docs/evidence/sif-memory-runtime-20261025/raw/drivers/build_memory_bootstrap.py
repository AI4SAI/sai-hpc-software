#!/usr/bin/env python3
"""Build the mountpoint-only SIF used by the system Apptainer userns profile."""
import argparse
from pathlib import Path
import subprocess
import tempfile


def build(destination):
    if not destination.is_absolute() or destination.resolve() != destination or destination.exists():
        raise ValueError('destination must be a new canonical absolute path')
    # All staging stays beside the requested image, never in physical /tmp.
    with tempfile.TemporaryDirectory(prefix='.memory-bootstrap-', dir=destination.parent) as directory:
        stage = Path(directory)
        root = stage / 'rootfs'
        root.mkdir()
        for name in ('usr', 'etc', 'opt', 'org', 'home', 'run', 'dev', 'proc', 'sys',
                     'tmp', 'var/tmp', '.singularity.d/env'):
            (root / name).mkdir(parents=True, exist_ok=True)
        for name, target in (('bin', 'usr/bin'), ('sbin', 'usr/sbin'),
                             ('lib', 'usr/lib'), ('lib64', 'usr/lib64')):
            (root / name).symlink_to(target)
        for name in ('passwd', 'group', 'hosts', 'resolv.conf', 'localtime'):
            (root / 'etc' / name).touch()
        squashfs = stage / 'rootfs.squashfs'
        subprocess.run(['mksquashfs', str(root), str(squashfs), '-processors', '1',
                        '-all-root', '-no-xattrs', '-noappend', '-no-progress'], check=True)
        subprocess.run(['apptainer', 'sif', 'new', str(destination)], check=True)
        # A pipe avoids the short file-to-file copy observed with tiny images
        # on the site's project filesystem. Verify all partition bytes before
        # declaring the image immutable; a valid superblock alone is insufficient.
        data = squashfs.read_bytes()
        subprocess.run(['apptainer', 'sif', 'add', '--datatype', '4', '--partfs', '1',
                        '--parttype', '2', '--partarch', '2', '--groupid', '1',
                        str(destination), '/dev/stdin'], input=data, check=True)
        copied = subprocess.check_output(['apptainer', 'sif', 'dump', '1', str(destination)])
        if copied != data:
            raise ValueError('SIF partition differs from the complete squashfs')
        destination.chmod(0o444)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    build(parser.parse_args().destination)
