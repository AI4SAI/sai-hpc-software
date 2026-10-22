#!/usr/bin/env python3
"""Freeze installation metadata after all three publications were reverified."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

PROJECT = Path('/home/stardust/sai-hpc-software')
ROOT = PROJECT / 'experimental/deepmd-lammps'
PACKAGE = Path(__file__).resolve().parent
BUILDS = {
    '4V100': 'md-39403117741-1-2026-10-20-9bf6ae82814537d7-export',
    '16V100': 'md-39403084059-1-2026-10-20-5fef92e054bacdaf-export',
    '8V100V0': 'md-39427644750-1-2026-10-20-1dfd6f53c113abda',
}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        stream.write(content)


def selector(choices):
    # Values are fixed canonical receipt paths, never user-provided Tcl.
    stem = 'SAI_LAMMPS_RELEASE_PARTITION'
    return '\n'.join([
        '#%Module1.0',
        'module-whatis "LAMMPS 30 Sep 2026 / DeePMD 3.2.0; V100 partition selection"',
        'conflict lammps',
        'if {[module-info mode load]} {',
        '    if {![info exists env(SLURM_JOB_PARTITION)]} {error "Load this module inside a Slurm allocation"}',
        '    set partition $env(SLURM_JOB_PARTITION)',
        '} elseif {[module-info mode remove]} {',
        f'    if {{![info exists env({stem})]}} {{error "Missing loaded LAMMPS partition"}}',
        f'    set partition $env({stem})',
        '} else {return}',
        'set fragments [dict create ' + ' '.join(f'{key} {{{path}}}' for key, path in choices.items()) + ']',
        'if {![dict exists $fragments $partition]} {error "Unsupported LAMMPS GPU partition: $partition"}',
        'set fragment [dict get $fragments $partition]',
        'if {![file isfile $fragment] || [file type $fragment] eq "link"} {error "Missing accepted LAMMPS installation"}',
        'source $fragment',
        f'setenv {stem} $partition', ''])


def main():
    deliveries, container = [], {}
    for partition, build in BUILDS.items():
        publication = ROOT / 'runs' / build / 'results/publication.json'
        manifest = json.loads(publication.read_text())
        recovery = json.loads((publication.parent / 'publication-export-recovery.json').read_text())
        assert recovery['state'] == 'published-and-reverified'
        assert recovery['publication_sha256'] == digest(publication)
        assert all(manifest[k] is True for k in ('published', 'build_verified', 'scientific_verified', 'performance_verified'))
        assert manifest['performance_scope'] == 'gpu' and manifest['performance_backends'] == ['pt']
        result = dict(partition=partition, build_run=build,
                      publication=str(publication.relative_to(PROJECT)), publication_sha256=digest(publication),
                      image=str(Path(manifest['artifact']).relative_to(PROJECT)), image_sha256=manifest['artifact_sha256'],
                      image_bytes=Path(manifest['artifact']).stat().st_size,
                      installations=[], modules=manifest['modules'])
        for item in manifest['native_export']['receipt']['installations']:
            prefix = item['expected_install_prefix']
            identity = next(identity for identity in manifest['delivery']['identities'].values()
                            if identity['install_prefix'] == prefix)
            assert identity['partition'] == partition
            source = Path(manifest['native_export']['directory']) / item['path']
            result['installations'].append(dict(prefix=prefix, identity=identity,
                source=str(source.relative_to(PROJECT)), manifest_sha256=item['manifest_sha256'],
                metadata_sha256={name: digest(source / name) for name in
                    ('share/sai/manifest.json', 'share/sai/export.json', 'share/sai/README-export.txt')}))
        container[partition] = manifest['modules']['lammps']['module_fragment']
        deliveries.append(result)
    release = dict(schema=1, version='stable_30Sep2026', deepmd_version='v3.2.0',
                   generated_at=datetime.now(timezone.utc).isoformat(), deliveries=deliveries)
    write(PACKAGE / 'release.json', json.dumps(release, sort_keys=True, indent=2) + '\n')
    # The native selector is maintained independently in this package. It must
    # not source workflow metadata from the public application installation.
    write(PACKAGE / 'modulefiles/lammps/30Sep2026-deepmd320-gpu', selector(container))
    print('Prepared three accepted GPU partitions; no physical /opt files modified.')


if __name__ == '__main__':
    main()
