#!/usr/bin/env python3
"""Use only explicitly activated and inventoried physical devtools providers."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess

from cp2k_devtools import TARGETS, digest, validate_provider
from cp2k_spack import GCC_PREFIX, validated_prefix
from devtools_install import require

LOCK = Path(__file__).with_suffix('.json')


def selected(partition, *, lock=LOCK, files=True):
    data = json.loads(Path(lock).read_text())
    require(data.get('schema') == 1 and set(data.get('partitions', {})) <= set(TARGETS), 'Invalid site provider lock')
    entry = data['partitions'].get(partition)
    if entry is None:
        return None
    provider = entry['provider']
    validate_provider(provider, files=files)
    require(provider['install']['partition'] == partition, 'Site provider belongs to another partition')
    require(isinstance(entry['native_flags'], str) and '-march=' in entry['native_flags'], 'Missing native CPU proof')
    repair = entry['libint_repair']
    libint = next(n for n in provider['install']['nodes'] if n['name'] == 'libint')
    require(repair['dag_hash'] == libint['hash'] and repair['local_source_patch_applied'] is True,
            'Libint source-repair evidence differs')
    return entry


def make_receipt(entry, partition, prefix):
    prefix = validated_prefix(partition, prefix, app_only=True)
    provider = deepcopy(entry['provider'])
    validate_provider(provider)
    require(provider['install']['partition'] == partition, 'Shared provider partition mismatch')
    data = deepcopy(provider['install'])
    data.update(layout='shared-devtools', install_prefix=prefix, shared_provider=provider,
                shared_provider_sha256=digest(provider))
    return data


def materialize(partition, prefix, results, *, lock=LOCK):
    entry = selected(partition, lock=lock)
    require(entry is not None, 'No activated shared dependency provider')
    observed = subprocess.check_output([GCC_PREFIX + '/bin/gcc', '-march=native',
                                       '-mtune=native', '-Q', '--help=target'], text=True).strip()
    require(observed == entry['native_flags'], 'Compute CPU flags differ from the shared library build')
    values = {'install-success.json': make_receipt(entry, partition, prefix),
              'libint-export-repair.json': entry['libint_repair']}
    for name in values:
        require(not (Path(results) / name).exists(), 'Dependency receipt already exists')
    for name, value in values.items():
        with (Path(results) / name).open('x') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write('\n')
    print('CP2K_SHARED_DEVTOOLS_REUSED ' + entry['provider']['contract_sha256'], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('op', choices=('available', 'materialize'))
    parser.add_argument('partition', choices=sorted(TARGETS))
    parser.add_argument('prefix', nargs='?')
    parser.add_argument('--results', type=Path, default=Path('/results'))
    args = parser.parse_args()
    if args.op == 'available':
        entry = selected(args.partition)
        print(json.dumps(dict(available=entry is not None,
                              contract_sha256=entry['provider']['contract_sha256'] if entry else None)))
    else:
        materialize(args.partition, args.prefix, args.results)


if __name__ == '__main__':
    main()
