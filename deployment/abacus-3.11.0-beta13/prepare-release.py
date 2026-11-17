#!/usr/bin/env python3
"""Freeze the accepted beta13 images and evidence in a readable admin package."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

sys.dont_write_bytecode = True
import install as deployment

IMAGES = {
    '16V100': '5d369351a325ac24d79eded428b1496a700cfbbec3894e6e444bdd44b5304c6c',
    '4V100': '2256cfe2fa2d054c6e381b9bd41cb3bb02833882b939fe801b566600a5ac27cb',
    '8V100V0': '1e5057004493d51d9b7fb800727c1996899b7678f2a986bf7da9a5d4f493bd91',
    'DSPRHBM': 'dc95da7a6d6d869d70ff28783427063ecfdef113f9229b5e9c567445fbadf3df',
}


def copy(source, destination, expected):
    deployment.plain(source)
    deployment.plain(destination)
    deployment.require(deployment.digest(source) == expected, 'source changed: ' + str(source))
    deployment.directories(destination.parent)
    if destination.exists():
        deployment.require(deployment.digest(destination) == expected, 'existing handoff differs')
    else:
        temporary = destination.with_name('.' + destination.name + '.incoming')
        with source.open('rb') as incoming, temporary.open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
        deployment.require(deployment.digest(temporary) == expected, 'handoff copy differs')
        temporary.chmod(0o444)
        temporary.rename(destination)
    destination.chmod(0o444)


def main():
    package, origin = deployment.PACKAGE, deployment.ORIGIN
    deployment.require(not (package / 'release.json').exists(), 'release is already frozen')
    root = origin / 'containers/software/abacus' / deployment.TRACK / deployment.BUILD_ID
    deliveries, plans = [], []
    for partition, expected_image in IMAGES.items():
        publications = [p for p in (root / partition).glob('*.json') if not p.name.endswith('.native.json')]
        deployment.require(len(publications) == 1, 'ambiguous release publication')
        publication, = publications
        record = json.loads(publication.read_text())
        identity, export = record['identity'], record['native_export']
        deployment.require(all(record[k] is True for k in ('verified', 'published', 'build_verified')),
                           'release not accepted')
        deployment.require(identity['partition'] == partition and identity['source_sha'] == deployment.SOURCE
                           and identity['build_id'] == deployment.BUILD_ID, 'wrong release identity')
        deployment.require(export['roundtrip_verified'] is True
                           and export['image_sha256'] == record['sha256'] == expected_image,
                           'native export is not accepted')
        image = Path(record['artifact'])
        receipt, = export['receipt']['installations']
        source = Path(export['directory']) / receipt['path']
        manifest_path = source / 'share/sai/manifest.json'
        manifest = deployment.pinned(manifest_path, receipt['manifest_sha256'])
        deployment.require(manifest['entry']['identity'] == identity, 'export identity mismatch')
        spec = dict(prefix=identity['install_prefix'], identity=identity,
                    source=str(source.relative_to(origin)), manifest='evidence/' + partition + '-manifest.json',
                    manifest_sha256=receipt['manifest_sha256'])
        rows, omitted = deployment.runtime_records(manifest, spec)
        deployment.verify_records(source, {r['path']: r for r in rows}, subset=True)
        public_image = package / 'images' / (partition + '.sif')
        public_record = package / 'evidence' / (partition + '-publication.json')
        copy(image, public_image, expected_image)
        copy(publication, public_record, deployment.digest(publication))
        copy(manifest_path, package / spec['manifest'], receipt['manifest_sha256'])
        deliveries.append(dict(partition=partition, build_run=publication.stem,
                               publication=str(public_record.relative_to(package)),
                               publication_sha256=deployment.digest(public_record),
                               image=str(public_image.relative_to(package)), origin_image=str(image),
                               image_sha256=expected_image, image_bytes=image.stat().st_size,
                               installations=[spec]))
        plans.append(dict(prefix=spec['prefix'], identity=identity, manifest_sha256=spec['manifest_sha256'],
                          files=rows, omitted_summary=deployment.omission_summary(omitted)))
        print(partition + ': pinned accepted SIF and runtime inventory', flush=True)
    release = dict(schema=1, version=deployment.VERSION, generated_at=datetime.now(timezone.utc).isoformat(),
                   module_sha256=deployment.digest(package / deployment.MODULE.name), deliveries=deliveries)
    with (package / 'release.json').open('x') as stream:
        json.dump(release, stream, indent=2, sort_keys=True); stream.write('\n')
    plan = dict(schema=1, policy=deployment.POLICY, release_sha256=deployment.digest(package / 'release.json'),
                installations=plans)
    with (package / 'runtime-plan.json').open('x') as stream:
        json.dump(plan, stream, indent=2, sort_keys=True); stream.write('\n')
    for name in ('release.json', 'runtime-plan.json'):
        (package / name).chmod(0o444)
    deployment.preflight(deployment.load_release(), package)
    print('Administrator package ready; physical /opt untouched.')


if __name__ == '__main__':
    main()
