#!/usr/bin/env python3
"""Replace only the two known NVIDIA ELPA prefixes, retaining both old trees."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

PACKAGE = Path(__file__).resolve().parent
base_path = PACKAGE / 'base_install.py'
if not base_path.exists():
    base_path = PACKAGE.with_name('elpa-2026.02.001-commfix') / 'install.py'
spec = importlib.util.spec_from_file_location('elpa_replacement_base', base_path)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--check', action='store_true')
    modes.add_argument('--install', action='store_true')
    modes.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    release = json.loads((PACKAGE / 'release.json').read_text())
    base.require(release['revision'] == 'backtransform-host-staging-1' and
                 set(release['builds']) == {'avx2', 'avx512'}, 'Unexpected replacement package')
    base.require(base.checksum(base_path) == release['base_installer_sha256'], 'Replacement implementation changed')
    base.require(base.checksum(base.MODULE) == release['module_sha256'], 'Public module changed')
    plan = []
    for isa, build in release['builds'].items():
        prefix = base.PREFIXES[isa]
        base.require(build['prefix'] == str(prefix), 'Unexpected library prefix')
        manifests = []
        for suffix, key in [('previous-manifest', 'previous_manifest_sha256'), ('manifest', 'manifest_sha256')]:
            path = PACKAGE / (isa + '-' + suffix + '.json')
            base.require(base.checksum(path) == build[key], 'Manifest changed')
            manifests.append(json.loads(path.read_text()))
        before, after = manifests
        state = base.installation_state(prefix, before, after)
        if args.verify:
            base.require(state == 'repaired', 'Candidate has not been installed: ' + str(prefix))
        else:
            payload = PACKAGE / 'payload' / isa
            base.verify(payload, after)
            base.require(base.checksum(payload / 'share/sai/gpu-backtransform-host-staging.patch') ==
                         release['backtransform_patch_sha256'], 'Candidate patch differs')
            backup = prefix.with_name('.' + prefix.name + '.before-' + build['previous_manifest_sha256'][:16])
            if state == 'previous':
                base.require(not backup.exists(), 'Backup already exists: ' + str(backup))
            plan.append((prefix, payload, before, after, build['previous_manifest_sha256']))
        size = sum(row.get('size', 0) for row in after)
        print(f'{isa}: {size/1024**2:.2f} MiB; current={state}; {prefix}', flush=True)
    if args.install:
        for entry in plan:
            base.replace_one(*entry)
        print('Installed both NVIDIA variants; run --verify, then compute-node acceptance.')
    elif args.verify:
        print('Physical NVIDIA inventories and public module verified; runtime acceptance is separate.')
    else:
        print('Preflight passed. Administrator: bash install.sh --install')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        sys.exit('Deployment stopped: ' + str(exc))
