#!/usr/bin/env python3
"""Prepare the compressed, login-readable administrator handoff from accepted SIFs."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
import install as deployment

ORIGIN = Path('/org/org-open-sai/proj-sai-hpc-software')
PUBLIC = Path('/home/stardust/sai-hpc-software/deployment/lammps-30Sep2026-update1')
TARGETS = {'4V100': '4v100-avx512', '16V100': '16v100-avx2', '8V100V0': '8v100v0-avx512'}


def save(path, content):
    deployment.directories(path.parent)
    if path.exists():
        deployment.require(path.read_bytes() == content, 'prepared file differs: ' + str(path))
    else:
        with path.open('xb') as stream:
            stream.write(content)
    path.chmod(0o444)


def save_json(path, data):
    save(path, (json.dumps(data, indent=2, sort_keys=True) + '\n').encode())


def copy(source, destination):
    expected = deployment.digest(source)
    deployment.directories(destination.parent)
    if destination.exists():
        deployment.require(deployment.digest(destination) == expected, 'public image differs')
    else:
        temporary = destination.with_name('.' + destination.name + '.incoming')
        with source.open('rb') as incoming, temporary.open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing, 1024**2)
        deployment.require(deployment.digest(temporary) == expected, 'image copy differs')
        temporary.chmod(0o444)
        temporary.rename(destination)
    destination.chmod(0o444)
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--controller', required=True, type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.controller))
    import lammps_acceptance as acceptance
    package = deployment.PACKAGE
    deployment.require(package == PUBLIC, 'prepare the public handoff at the reviewed path')
    deliveries, build_info = [], {}
    index = ['LAMMPS stable_30Sep2026_update1; commit ' + deployment.SOURCE,
             'Read on login nodes without loading a module.',
             'Features: MPI/OpenMP; GPU + KOKKOS CUDA SM70; DeePMD 3.2.0; PLUMED; Python;',
             'REAXFF; ML-IAP/ML-SNAP; 71 packages; 2473 styles.', '']
    for part, target in TARGETS.items():
        run = 'lmp-41589099639-1-release-' + target + '-20261103-098ffdefa525'
        build = ORIGIN / 'experimental/deepmd-lammps/runs' / run
        science = ORIGIN / 'experimental/deepmd-lammps/runtime-tests' / (run + '-science')
        proof = acceptance.verify(science)
        request = json.loads((build / 'request.json').read_text())
        identity = request['delivery']['identity']
        deployment.require(identity['source_sha'] == deployment.SOURCE
                           and identity['partition'] == part, 'wrong accepted release')
        image = build / 'candidate.sif'
        prefix = identity['install_prefix']
        embedded = subprocess.check_output(['unsquashfs', '-processors', '2', '-no-progress',
            '-o', str(deployment.image_offset(image)), '-cat', str(image),
            prefix.lstrip('/') + '/share/sai/manifest.json'])
        manifest = json.loads(embedded)
        captured = json.loads((science / 'results/inventory/features.json').read_text())
        deployment.require(manifest == captured['installed_manifest'], 'SIF manifest differs from science capture')
        files, omitted = deployment.runtime_records(manifest, dict(identity=identity, prefix=prefix))
        dependency = request['delivery']['deepmd_dependency']
        plan_file = Path(request['controller']) / 'runtime-plan.json'
        deployment.require(deployment.digest(plan_file) == dependency['runtime_plan_sha256'], 'dependency plan changed')
        plan = json.loads(plan_file.read_text())
        dep, = [r for r in plan['installations'] if r['identity'] == dependency['identity']]
        records = dict(identity=dep['identity'], files=dep['files'])
        paths = dict(image='images/' + part + '.sif', manifest='evidence/' + part + '-manifest.json',
                     acceptance='evidence/' + part + '-acceptance.json',
                     verification='evidence/' + part + '-verification.json',
                     dependency_records='evidence/' + part + '-deepmd.json')
        save(package / paths['manifest'], embedded)
        save(package / paths['acceptance'], (science / 'acceptance.json').read_bytes())
        save(package / paths['verification'], (science / 'verification.json').read_bytes())
        save_json(package / paths['dependency_records'], records)
        image_sha = copy(image, package / paths['image'])
        deployment.require(image_sha == proof['artifact_sha256'], 'science/image mismatch')
        row = dict(partition=part, identity=identity, prefix=prefix, dependency=dependency,
                   science=str(science), build=str(build), origin_image=str(image),
                   image_bytes=image.stat().st_size, files=files, omitted=omitted, **paths)
        row.update({name + '_sha256': deployment.digest(package / path) for name, path in paths.items()})
        deliveries.append(row)
        name = 'build-info/' + part + '.txt'
        text = ('LAMMPS stable_30Sep2026_update1\nCommit: ' + deployment.SOURCE + '\n'
                + 'Partition: ' + part + '\nInstallation: ' + prefix + '\n'
                + 'DeePMD: ' + dependency['identity']['install_prefix'] + '\n'
                + 'SIF SHA256: ' + image_sha + '\nAccepted science job: ' + proof['job']
                + '\n\nRecorded lmp -h output:\n\n'
                + (science / 'results/version/stdout.txt').read_text())
        save(package / name, text.encode()); build_info[name] = deployment.digest(package / name)
        index += [part + ':', '  installation: ' + prefix,
                  '  potentials: ' + prefix + '/share/lammps/potentials',
                  '  complete build options: cat ' + str(PUBLIC / name), '']
        print(part + ': compressed handoff prepared', flush=True)
    save(package / 'build-info/index.txt', ('\n'.join(index) + '\n').encode())
    build_info['build-info/index.txt'] = deployment.digest(package / 'build-info/index.txt')
    release = dict(schema=1, version='stable_30Sep2026_update1', deliveries=deliveries,
                   module_sha256=deployment.digest(package / deployment.MODULE.name), build_info=build_info)
    save_json(package / 'release.json', release)
    deployment.preflight(deployment.load_release())


if __name__ == '__main__':
    main()
