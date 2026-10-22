#!/usr/bin/env python3
"""Pin the four accepted beta11 exports without writing to physical /opt."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
import install as deployment

IMAGES = {
    '4v100-avx512': '80a412ba5ba161cc01f10fa34aa3b0e4fc5942976f2d1d08bed6180df6aee91f',
    '16v100-avx2': '45e759b8c88dece5e35b3a68c3ab5c4939062d2dd49d8dbeffc66de0cb0e2662',
    '8v100v0-avx512': '7ec3ffa4a8e4191dd6e03084b7a0a0b25d38dcdcc67767d83d3080ac3c7a1e60',
    'dsprhbm': 'ae2e91a6c0fee9fa8a8cfd9a4e5a08246c7967685fcebab31184c11a5f7b1637',
}


def main():
    root = deployment.PROJECT
    deliveries = []
    for target, expected_image in IMAGES.items():
        run_id = '39808766467-1-release-' + target + '-2026-10-22-b0411118d360'
        image = Path((root / 'runs' / run_id / 'artifact.path').read_text().strip())
        publication = image.with_suffix('.json')
        record = json.loads(publication.read_text())
        deployment.require(record['sha256'] == expected_image == deployment.digest(image), 'image changed')
        deployment.require(all(record[k] is True for k in ('verified', 'published', 'build_verified')),
                           'release not accepted')
        export = record['native_export']
        deployment.require(export['roundtrip_verified'] is True and export['image_sha256'] == expected_image,
                           'native export not accepted')
        identity = record['identity']
        deployment.require(identity['target'] == target and identity['build_id'] == deployment.BUILD_ID,
                           'wrong build identity')
        receipt, = export['receipt']['installations']
        source = Path(export['directory']) / receipt['path']
        metadata = {name: deployment.digest(source / name) for name in
                    ('share/sai/manifest.json', 'share/sai/export.json', 'share/sai/README-export.txt')}
        deployment.require(metadata['share/sai/manifest.json'] == receipt['manifest_sha256'], 'manifest changed')
        native = deployment.pinned(source / 'share/sai/manifest.json', receipt['manifest_sha256'])
        deployment.require(native['entry']['identity'] == identity, 'export identity mismatch')
        spec = dict(prefix=identity['install_prefix'], identity=identity, source=str(source.relative_to(root)),
                    manifest_sha256=receipt['manifest_sha256'], metadata_sha256=metadata)
        deliveries.append(dict(partition=identity['partition'], build_run=run_id,
                               publication=str(publication.relative_to(root)),
                               publication_sha256=deployment.digest(publication),
                               image=str(image.relative_to(root)), image_sha256=expected_image,
                               image_bytes=image.stat().st_size, installations=[spec]))
    release = dict(schema=1, version=deployment.VERSION, generated_at=datetime.now(timezone.utc).isoformat(),
                   module_sha256=deployment.digest(deployment.PACKAGE / deployment.MODULE.name), deliveries=deliveries)
    with (deployment.PACKAGE / 'release.json').open('x') as stream:
        json.dump(release, stream, indent=2, sort_keys=True)
        stream.write('\n')
    deployment.load_release()
    print('Pinned four accepted ABACUS exports; physical /opt untouched.')


if __name__ == '__main__':
    main()
