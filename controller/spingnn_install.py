"""Package and check an administrator's native install; never rebuild code."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile

from export_native import verify_export
from spingnn_package import version_module
from spingnn_release import checksum, immutable, plain, qualified_identity, require, save


def bundle(directory, identity, exported, control, files):
    identity = qualified_identity(identity)
    payload = directory / 'payload.tar.gz'
    metadata = directory / 'bundle.json'
    module = directory / 'version-module.lua'
    save(metadata, dict(schema=1, identity=identity, export=exported))
    immutable(module, version_module(identity).encode())
    if not payload.exists():
        temporary = directory / '.payload.tar.gz'
        with tarfile.open(temporary, 'w:gz') as archive:
            archive.add(directory / 'native', arcname='native')
            archive.add(metadata, arcname='bundle.json')
            archive.add(module, arcname='version-module.lua')
            for name in files:
                archive.add(control / name, arcname='controller/' + name)
        temporary.chmod(0o444)
        os.link(temporary, payload)
        temporary.unlink()
    digest = checksum(payload)
    # The two-file handoff is deliberately simple: verified archive + Bash.
    script = '\n'.join(['#!/bin/bash', 'set -euo pipefail',
        'mode=${1:---check}', '[[ $mode == --check || $mode == --install ]] || exit 2',
        'archive=${2:-"$(dirname -- "$(readlink -f -- "$0")")/payload.tar.gz"}',
        'test -f "$archive"',
        "printf '%s  %s\\n' " + digest + ' "$archive" | sha256sum --check --status',
        'stage=$(mktemp -d)', 'trap \'rm -rf -- "$stage"\' EXIT',
        'tar --no-same-owner -xzf "$archive" -C "$stage"',
        'python3 -B "$stage/controller/spingnn_install.py" "$mode" "$stage"', ''])
    immutable(directory / 'install.sh', script.encode())
    return {name: checksum(directory / name) for name in ('payload.tar.gz', 'install.sh')}


def check(root):
    root = plain(root)
    record = json.loads((root / 'bundle.json').read_text())
    require(record.get('schema') == 1, 'invalid SpinGNN install bundle')
    identity = qualified_identity(record['identity'])
    exported = record['export']
    require(exported['identities'] == [identity], 'export identity differs from administrator bundle')
    verify_export(root / 'native', exported['receipt'], exported['image_sha256'], [identity])
    require((root / 'version-module.lua').read_text() == version_module(identity), 'administrator module differs')
    return record


def install(root, destination):
    record = check(root)
    identity = record['identity']
    destination = plain(destination)
    require(destination.is_dir(), 'DESTDIR must be an existing directory')
    prefix = plain(destination / identity['install_prefix'].lstrip('/'))
    version = Path(identity['install_prefix']).parent.name.removeprefix('v')
    module = plain(destination / 'opt/modules/modulefiles/apps/spingnn' / (version + '.lua'))
    text = version_module(identity).encode()
    require(not prefix.exists(), 'version/partition already installed; automatic replacement is forbidden')
    if module.exists():
        require(module.is_file() and module.read_bytes() == text, 'existing public module differs; do not overwrite')
    for path in (prefix.parent, module.parent):
        path.mkdir(parents=True, exist_ok=True)
        plain(path)
    with (prefix.parent / '.install.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        require(not prefix.exists() and not prefix.is_symlink(), 'concurrent installation exists')
        with tempfile.TemporaryDirectory(prefix='.spingnn-install-', dir=prefix.parent) as staging:
            candidate = Path(staging) / identity['partition']
            shutil.copytree(root / 'native', candidate, symlinks=True)
            exported = record['export']
            verify_export(candidate, exported['receipt'], exported['image_sha256'], [identity])
            candidate.rename(prefix)
        immutable(module, text)
    return dict(installed=str(prefix), module=str(module), image_sha256=record['export']['image_sha256'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true')
    group.add_argument('--install', action='store_true')
    parser.add_argument('bundle', type=Path)
    args = parser.parse_args()
    result = (dict(checked=True, identity=check(args.bundle)['identity']) if args.check else
              install(args.bundle, Path(os.environ.get('DESTDIR', '/'))))
    print(json.dumps(result, sort_keys=True))
