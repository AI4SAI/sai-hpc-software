#!/usr/bin/env python3
"""Restore manifest-declared read-only modes after a separate fakeroot session."""
import json
import os
from pathlib import Path
import stat
import sys

from export_native import (MANIFEST_PATH, aggregate_manifests, inventory,
                           parse_manifest, read_installed_manifests)


def prepare(root, identities):
    root = Path(root).resolve(strict=True)
    manifests, paths = [], []
    for identity in identities:
        path = root / identity['install_prefix'].lstrip('/') / MANIFEST_PATH
        if (path.resolve(strict=True) != path or not path.is_file()
                or stat.S_IMODE(path.stat().st_mode) not in (0o444, 0o644)):
            raise ValueError('unexpected packaged manifest path or mode')
        manifest = parse_manifest(path.read_bytes())
        if manifest.get('entry', {}).get('identity') != identity:
            raise ValueError('packaged identity differs from locked delivery')
        manifests.append(manifest)
        paths.append(path)
    expected = aggregate_manifests(manifests)
    observed = inventory(expected['entries'], root)
    actual = {row['path']: row for row in observed['files']}
    changes = []
    if set(actual) != {row['path'] for row in expected['files']}:
        raise ValueError('packaged file set differs from installed inventory')
    for row in expected['files']:
        candidate = actual[row['path']]
        if {key: value for key, value in row.items() if key != 'mode'} != {
                key: value for key, value in candidate.items() if key != 'mode'}:
            raise ValueError('packaged content differs from installed inventory: ' + row['path'])
        if candidate['mode'] not in (row['mode'], row['mode'] | 0o200):
            raise ValueError('unexpected packaged permission change: ' + row['path'])
        if candidate['mode'] != row['mode']:
            changes.append((root / row['path'], row['mode']))
    # Validate every byte, link and permission difference before changing any
    # mode. fakeroot keeps owner-write on the backing file across sessions;
    # only remove that extra bit, without altering any payload or manifest.
    for path, mode in changes:
        path.chmod(mode)
    for path in paths:
        path.chmod(0o444)
    if read_installed_manifests(expected['entries'], root) != inventory(expected['entries'], root):
        raise ValueError('packaged inventory still differs after real chmod')
    return dict(files_verified=len(actual), payload_modes_restored=len(changes),
                manifest_modes_verified=len(paths), payload_bytes_unchanged=True)


def main():
    if os.environ.get('LD_PRELOAD'):
        raise ValueError('packaging permissions require a process without LD_PRELOAD')
    from md_controller import validate_pair
    if len(sys.argv) != 2 or sys.argv[1] != '/workspace/export':
        raise ValueError('packaging must stay in /workspace/export')
    delivery = validate_pair(json.loads(os.environ['SAI_MD_DELIVERY']))
    print('MD_PACKAGED_MODES_VERIFIED ' + json.dumps(
        prepare(sys.argv[1], list(delivery['identities'].values())), sort_keys=True))


if __name__ == '__main__':
    main()
