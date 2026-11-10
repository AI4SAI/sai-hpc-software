#!/usr/bin/env python3
"""Prepare a readable administrator package from four immutable accepted SIFs."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

sys.dont_write_bytecode = True
import install as deployment

ORIGIN = Path('/org/org-open-sai/proj-sai-hpc-software')
RUNS = {
    '16V100': 'qe-release-20261105-r7-16v100-avx2',
    '4V100': 'qe-release-20261105-r7-4v100-avx512',
    '8V100V0': 'qe-release-20261105-r7-8v100v0-avx512',
    'DSPRHBM': 'qe-release-20261105-r10-dsprhbm',
}


def copy(source, destination):
    expected = deployment.digest(source)
    deployment.directories(destination.parent)
    if destination.exists():
        deployment.require(deployment.digest(destination) == expected, 'existing public payload differs')
    else:
        temporary = destination.with_name('.' + destination.name + '.incoming')
        with source.open('rb') as incoming, temporary.open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
        deployment.require(deployment.digest(temporary) == expected, 'copy checksum differs')
        temporary.chmod(0o444)
        temporary.rename(destination)
    destination.chmod(0o444)
    return expected


def main():
    package = deployment.PACKAGE
    deployment.require(not (package / 'release.json').exists(), 'release is already frozen')
    deliveries, native = [], {}
    for partition, run in RUNS.items():
        image = Path((ORIGIN / 'runs' / run / 'artifact.path').read_text().strip())
        publication = image.with_suffix('.json')
        record = json.loads(publication.read_text())
        deployment.require(all(record[key] is True for key in ('verified', 'published', 'build_verified'))
                           and record['qe_science']['verified'] is True, 'unaccepted release')
        deployment.require(deployment.digest(image) == record['sha256'], 'image changed')
        export = record['native_export']
        deployment.require(export['roundtrip_verified'] is True, 'unverified native export')
        receipt, = export['receipt']['installations']
        source = Path(export['directory']) / receipt['path']
        manifest = deployment.pinned(source / 'share/sai/manifest.json', receipt['manifest_sha256'])
        identity = record['identity']
        deployment.require(identity['partition'] == partition and identity['source_sha'] == deployment.SOURCE
                           and manifest['entry']['identity'] == identity, 'source/partition mismatch')
        # Every command must already resolve all libraries outside its private
        # prefix. This proves removing static development files is appropriate.
        inspection = json.loads((source / 'share/sai/binary-inspection.json').read_text())
        aliases = {name.removeprefix('bin/') for name in deployment.COMMAND_ALIASES}
        deployment.require(set(inspection) == set(manifest['entry']['commands']) - aliases,
                           'incomplete regular executable inspection')
        for name, proof in inspection.items():
            binary = next(row for row in manifest['files'] if row['path'] == identity['install_prefix'].lstrip('/') + '/bin/' + name)
            deployment.require(proof['sha256'] == binary['sha256'] and 'not found' not in proof['ldd']
                               and identity['install_prefix'] + '/' not in proof['ldd'],
                               'executable still needs private runtime files: ' + name)
        files = deployment.runtime_records(manifest, identity['install_prefix'].lstrip('/'))
        output_image = package / 'images' / (partition + '.sif')
        output_publication = package / 'evidence' / (partition + '-publication.json')
        output_manifest = package / 'evidence' / (partition + '-manifest.json')
        image_sha = copy(image, output_image)
        publication_sha = copy(publication, output_publication)
        manifest_sha = copy(source / 'share/sai/manifest.json', output_manifest)
        deliveries.append(dict(partition=partition, identity=identity, prefix=identity['install_prefix'],
                               build_run=run, image=str(output_image.relative_to(package)), image_sha256=image_sha,
                               image_bytes=image.stat().st_size, origin_image=str(image),
                               publication=str(output_publication.relative_to(package)), publication_sha256=publication_sha,
                               manifest=str(output_manifest.relative_to(package)), manifest_sha256=manifest_sha, files=files))
        native[partition] = manifest
        print(partition + ': copied accepted compressed image', flush=True)
    cpu = native['DSPRHBM']
    license = deployment.hdf5_license(cpu)
    for row in deliveries:
        row['files'] = deployment.with_hdf5_license(row['files'], license)
    release = dict(schema=1, software='qe', version='qe-7.6', generated_at=datetime.now(timezone.utc).isoformat(),
                   module_sha256=deployment.digest(package / deployment.MODULE.name),
                   hdf5_license=license, deliveries=deliveries)
    with (package / 'release.json').open('x') as stream:
        json.dump(release, stream, indent=2, sort_keys=True); stream.write('\n')
    (package / 'release.json').chmod(0o444)
    deployment.preflight(deployment.load_release(), package)
    print('Administrator package ready; physical /opt untouched.')


if __name__ == '__main__':
    main()
