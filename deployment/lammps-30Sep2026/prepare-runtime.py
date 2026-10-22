#!/usr/bin/env python3
"""Create the reviewed runtime-only inventory; never modify original exports."""
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
import install as deployment


def main():
    release = deployment.load_release()
    installations = []
    for delivery in release['deliveries']:
        for spec in delivery['installations']:
            source = deployment.PROJECT / spec['source']
            manifest = deployment.pinned(source / 'share/sai/manifest.json', spec['manifest_sha256'])
            rows, removed = deployment.runtime_records(manifest, spec)
            entry = dict(prefix=spec['prefix'], identity=spec['identity'], manifest_sha256=spec['manifest_sha256'],
                         files=rows, omitted=removed)
            installations.append(entry)
            print(delivery['partition'], spec['identity']['software'],
                  sum(row['kind'] == 'file' for row in rows), 'runtime files;',
                  round(sum(row.get('size', 0) for row in rows) / 1024**2, 2), 'MiB;', len(removed), 'omitted')
    with (deployment.PACKAGE / 'runtime-plan.json').open('x') as stream:
        json.dump(dict(schema=1, policy=deployment.POLICY, release_sha256=deployment.digest(deployment.PACKAGE / 'release.json'),
                       installations=installations), stream, sort_keys=True, indent=2)
        stream.write('\n')


if __name__ == '__main__':
    main()
