#!/usr/bin/env python3
"""Record the pinned source plus the normal DeePMD registration adaptation."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

PINS = dict(base='d291d572cf3c73a8a720774b78482a7c931304ca',
            pr5308='f1a6e5cf5167deeb1f70bc8bd38ff8c2e1a853a7')


def source():
    git = ['git', '-C', '/workspace/lammps']
    sha = subprocess.check_output([*git, 'rev-parse', 'HEAD'], text=True).strip()
    if sha != PINS[os.environ['SAI_BENCH_VARIANT']]:
        raise ValueError('unexpected source revision')
    files = subprocess.check_output([*git, 'diff', '--name-only'], text=True).splitlines()
    if files != ['cmake/CMakeLists.txt']:
        raise ValueError('unexpected tracked source adaptation')
    before = subprocess.check_output([*git, 'show', 'HEAD:cmake/CMakeLists.txt'], text=True)
    sys.path.insert(0, '/control')
    from md_registration import integrate
    actual = Path('/workspace/lammps/cmake/CMakeLists.txt').read_text()
    if actual != integrate(before):
        raise ValueError('source differs from upstream plus normal DeePMD registration')
    Path('/runtime/source-local.diff').write_bytes(subprocess.check_output([*git, 'diff', '--binary']))
    Path('/runtime/source.json').write_text(json.dumps(dict(sha=sha, variant=os.environ['SAI_BENCH_VARIANT'],
        cmake_sha256=hashlib.sha256(actual.encode()).hexdigest(), adaptation='standard DeePMD registration only'), indent=2) + '\n')


def collect():
    root = Path('/workspace/lammps-build')
    output = Path('/runtime')
    for name in ('CMakeCache.txt', 'compile_commands.json'):
        if (root / name).is_file():
            shutil.copyfile(root / name, output / name)
    for path in root.rglob('*.rsp'):
        dest = output / 'cmake-response-files' / path.relative_to(root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
    db = root / 'compile_commands.json'
    if db.is_file():
        rows = json.loads(db.read_text())
        host = [row['file'] for row in rows if '--host-only' in shlex.split(row['command'])]
        (output / 'host-only.json').write_text(json.dumps(dict(commands=len(rows), host_only=host), indent=2) + '\n')
        if (os.environ['SAI_BENCH_VARIANT'] == 'base' and host) or (os.environ['SAI_BENCH_VARIANT'] == 'pr5308' and not host):
            raise ValueError('unexpected host-only compiler selection')


if __name__ == '__main__':
    {'source': source, 'collect': collect}[sys.argv[1]]()
