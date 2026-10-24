#!/usr/bin/env python3
"""Recreate the reviewed snapshot from the campaign's checked-in evidence."""
import json
from pathlib import Path
import sys

PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parents[1]
sys.path.insert(0, str(REPO / 'controller'))
from sif_modules import validate_snapshot
from sif_runtime import digest


def read(relative):
    path = REPO / 'docs/evidence' / relative
    return path, json.loads(path.read_text())


def snapshot():
    result = dict(schema=1, snapshot='20261024', applications={})

    def add(identity, image, sha, evidence, *, binds=(), build=True, science=True, **extra):
        software, partition = identity['software'], identity['partition']
        app = result['applications'].setdefault(software, dict(
            version=identity['source_ref'] + '-20261024-g' + identity['source_sha'][:12], partitions={}))
        row = dict(identity=identity, image=image, sha256=sha, binds=list(binds),
                   build_verified=build, scientific_verified=science,
                   evidence=str(evidence.relative_to(REPO)), evidence_sha256=digest(evidence), **extra)
        if software != 'gpumd':
            row['mpi_prefix'] = '/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-' + identity['dependency_isa']
        app['partitions'][partition] = row

    for software in ('abacus', 'cp2k'):
        for partition in ('4V100', '16V100', '8V100V0', 'DSPRHBM'):
            proof, data = read(f'{software}-development-20261023/{partition}-validation.json')
            assert data['verified'] and data['scientific_verified'] and data['project_catalog_published']
            add(data['identity'], data['artifact'], data['artifact_sha256'], proof,
                binds=('/opt/apps/conda_env/deepmd-kit-3.1.2',) if software == 'abacus' else ())
    proof, data = read('gpumd-project-20261023/development.json')
    for row in data['rows']:
        manifest = row['manifest']
        assert row['independent_revalidation'] == 'ACCEPTANCE_AND_NATIVE_EXPORT_REVERIFIED'
        add(manifest['identity'], manifest['artifact'], manifest['sha256'], proof,
            binds=('/opt/apps/conda_env/deepmd-kit-3.2.0', '/opt/apps/plumed/plumed-2.10.1',
                   '/opt/apps/gpumd/GPUMD-5.8'), build=manifest['build_verified'],
            science=manifest['gpumd_science']['verified'])
    proof, data = read('lammps-project-20261023/delivery.json')
    for row in data['targets']:
        add(row['identity'], row['artifact'], row['artifact_sha256'], proof,
            binds=('/opt/apps/conda_env/deepmd-kit-3.2.0', '/opt/apps/conda_env/VeloxChem/gpu',
                   '/opt/apps/plumed/plumed-2.10.1', row['installed_deepmd_dependency']['identity']['install_prefix']),
            build=row['build_verified'], science=row['scientific_verified'],
            installed_deepmd_dependency=row['installed_deepmd_dependency'])
    proof, data = read('deepmd-project-20261023/delivery.json')
    for partition, row in data['targets'].items():
        filename = partition + ('-raw-files.json' if partition == '8V100V0' else '-provenance.json')
        _, provenance = read('deepmd-project-20261023/' + filename)
        [environment_record] = [v for k, v in provenance['raw_files'].items() if k.endswith('/runtime-env.sh')]
        environment = PACKAGE / 'environments' / (partition + '.sh')
        assert digest(environment) == environment_record['sha256']
        add(row['identity'], row['artifact'], row['artifact_sha256'], proof,
            binds=('/opt/apps/conda_env/deepmd-kit-3.2.0', '/opt/apps/conda_env/VeloxChem/gpu',
                   '/opt/apps/plumed/plumed-2.10.1'), build=row['build_verified'], science=row['scientific_verified'],
            environment=environment.read_text(), environment_sha256=digest(environment))
    assert {k: len(v['partitions']) for k, v in result['applications'].items()} == {
        'abacus': 4, 'cp2k': 4, 'gpumd': 3, 'lammps': 3, 'deepmd-kit': 3}
    return validate_snapshot(result)


if __name__ == '__main__':
    (PACKAGE / 'snapshot.json').write_text(json.dumps(snapshot(), indent=2, sort_keys=True) + '\n')
