#!/usr/bin/env python3
"""Package only the installed runtime in a minimal SIF filesystem."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

run = Path(sys.argv[1]).resolve()
prefix = run / 'prefix'
source = Path(sys.argv[2]).resolve() if len(sys.argv) == 3 else None
if source is not None:
    subprocess.run(['cp', '-al', str(source / 'prefix'), str(prefix)], check=True)
    shutil.copytree(source / 'runtime', run / 'runtime', ignore=shutil.ignore_patterns(
        'GPUMDkit-*', 'conda-pkgs', 'pip-cache', 'tmp', 'apptainer-*', 'sif-acceptance'))
# Project directories inherit setgid, which is not part of the application.
for directory in [prefix, *[p for p in prefix.rglob('*') if p.is_dir() and not p.is_symlink()]]:
    directory.chmod(0o755)
if (prefix / 'conda-meta').exists():
    shutil.copytree(prefix / 'conda-meta', run / 'conda-metadata')
    shutil.rmtree(prefix / 'conda-meta')
base = Path('/org/org-open-sai/proj-sai-hpc-software/containers/base/minimal-v1.sif')
listing = subprocess.check_output(['apptainer', 'sif', 'list', str(base)], text=True)
offset, = re.findall(r'\|\s*(\d+)-\d+\s*\|\s*FS \(Squashfs/\*System/', listing)
subprocess.run(['unsquashfs', '-processors', '2', '-no-xattrs', '-o', offset,
                '-d', str(run / 'rootfs'), str(base)], check=True)
target = run / 'rootfs/opt/apps/GPUMDkit/1.5.9'
target.parent.mkdir(parents=True)
subprocess.run(['cp', '-al', str(prefix), str(target)], check=True)
inventory = []
for path in [prefix, *sorted(prefix.rglob('*'))]:
    info = path.lstat()
    row = dict(path=str(path.relative_to(prefix)), mode=stat.S_IMODE(info.st_mode))
    if path.is_symlink():
        row.update(kind='symlink', target=os.readlink(path))
    elif path.is_dir():
        row.update(kind='directory')
    elif path.is_file():
        with path.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        row.update(kind='file', size=info.st_size, sha256=checksum)
    else:
        raise ValueError('unexpected special file ' + str(path))
    inventory.append(row)
(run / 'inventory.json').write_text(json.dumps(inventory, indent=2) + '\n')
subprocess.run(['mksquashfs', str(run / 'rootfs'), str(run / 'rootfs.squashfs'),
                '-noappend', '-all-root', '-no-xattrs', '-processors', '2', '-quiet'], check=True)
