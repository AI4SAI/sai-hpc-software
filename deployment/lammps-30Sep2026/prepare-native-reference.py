#!/usr/bin/env python3
"""Pin existing accepted inputs/results for later physical native checks."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

import install as deployment


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=deployment.PROJECT)
    parser.add_argument('--repository', type=Path, required=True)
    args = parser.parse_args()
    project = args.project.resolve(strict=True)
    root = project / 'experimental/deepmd-lammps'
    release = deployment.load_release()
    result = dict(schema=1, release_sha256=deployment.digest(deployment.PACKAGE / 'release.json'), partitions={})
    for item in release['deliveries']:
        partition, build = item['partition'], item['build_run']
        publication = deployment.pinned(project / item['publication'], item['publication_sha256'])
        proof = publication['acceptance']['science']
        control = Path(publication['runtime_launcher']).parent
        sys.path.insert(0, str(control))
        import md_science as science
        import md_evidence as evidence
        tools = {str((control / name).relative_to(project)): deployment.digest(control / name)
                 for name in ('md_science.py', 'md_evidence.py', 'md_probe.py')}
        gpu_helper = args.repository / 'docs/evidence/lammps-release-20261020/gpu_functional_smoke.py'
        gpu = load('gpu_reference', gpu_helper)
        tools[str(gpu_helper.relative_to(project))] = deployment.digest(gpu_helper)
        task = root / 'runtime-tests' / proof['run_id']
        files = {}

        def pin(path, digest=None):
            deployment.plain(path)
            actual = deployment.digest(path)
            deployment.require(digest is None or digest == actual, 'accepted evidence changed: ' + str(path))
            files[str(path.relative_to(project))] = actual
            return path

        case_files = {}
        references = {}
        for backend in science.BACKENDS:
            fixture = science._load_fixture(task / 'case', backend)
            deployment.require(fixture['tolerances'] == science.TOLERANCES, 'scientific tolerance changed')
            names = ('fixture.json', 'data.lmp', 'plumed.dat', 'in.' + backend, science.BACKENDS[backend])
            for name in names:
                source = task / 'case' / name
                for path in sorted(source.rglob('*')) if source.is_dir() else [source]:
                    if path.is_file():
                        case_files[str(path.relative_to(task / 'case'))] = deployment.digest(pin(path))
            references[backend] = {}
            for engine in ('python', 'lammps'):
                relative = f'results/candidate-{backend}-{engine}.json'
                records = json.loads(pin(task / relative, proof['evidence']['files'][relative]).read_text())
                expected = records[1]['observables']
                deployment.require(records[1]['input_sha256'] == fixture['models'][backend]['input_sha256']
                                   and records[1]['tolerances'] == science.TOLERANCES, 'reference model/tolerance changed')
                if engine == 'lammps':
                    trial = task / 'results' / f'candidate-{backend}-trials/1'
                    for name in ('stdout.txt', 'result.dump', 'COLVAR'):
                        pin(trial / name, proof['evidence']['files'][str((trial / name).relative_to(task))])
                    observed = science.parse_lammps_output((trial / 'stdout.txt').read_text(),
                                                          (trial / 'result.dump').read_text())
                    deployment.require(observed == expected, 'raw accepted LAMMPS result differs')
                    science.verify_plumed_output((trial / 'COLVAR').read_text(), fixture['distance_angstrom'])
                for name, tolerance in science.TOLERANCES.items():
                    evidence._compare_numeric(expected[name], fixture['reference'][name], **tolerance, path=name)
                references[backend][engine] = expected
        gpu_proof = json.loads((args.repository / 'docs/evidence/lammps-release-20261020' /
                               {'4V100': '4v-gpu-functional.json', '16V100': '16v-gpu-functional.json',
                                '8V100V0': '8v-gpu-functional.json'}[partition]).read_text())
        deployment.require(gpu_proof['passed'] and gpu_proof['artifact_sha256'] == item['image_sha256'],
                           'GPU reference differs from accepted image')
        gpu_task = root / 'runtime-tests' / (build + '-gpu-functional')
        gpu_cases = {}
        for potential in ('lj', 'coulomb-pppm'):
            for mode in ('gpu', 'kokkos'):
                name = potential + '-candidate-' + mode
                folder = gpu_task / name
                for filename in ('data.lmp', 'in.lmp', 'stdout.txt', 'result.dump'):
                    pin(folder / filename, gpu_proof['files'][name + '/' + filename])
                gpu_cases[name] = dict(source=str(folder.relative_to(project)), mode=mode,
                                       expected=gpu.values(folder))
        spec = next(spec for spec in item['installations'] if spec['identity']['software'] == 'lammps')
        source = project / spec['source']
        manifest = deployment.pinned(source / 'share/sai/manifest.json', spec['manifest_sha256'])
        candidate = source / 'share/sai/candidate.json'
        candidate_record = next(row for row in manifest['files'] if row['path'] ==
                                spec['prefix'].lstrip('/') + '/share/sai/candidate.json')
        inventory = json.loads(pin(candidate, candidate_record['sha256']).read_text())['lammps']
        features = {name: inventory[name] for name in ('version', 'packages', 'styles')}
        binary = next(row for row in manifest['files'] if row['path'] == spec['prefix'].lstrip('/') + '/bin/lmp')
        final = json.loads(pin(args.repository / 'docs/evidence/lammps-release-20261020/final-delivery.json').read_text())
        delivered = next(d for d in final['deliveries'] if d['partition'] == partition)
        deployment.require(delivered['artifact_sha256'] == item['image_sha256'], 'published module uses a different image')
        help_path = root / 'runtime-tests' / (build + '-published-module/results/help.txt')
        help_text = pin(help_path, delivered['public_module']['help_sha256']).read_text()
        body = re.findall(r'(?ms)^\* Body styles:\s*\n([^*]+?)(?=^\*)', help_text)
        deployment.require(len(body) == 1, 'missing unique Body styles section')
        result['partitions'][partition] = dict(build_run=build, image_sha256=item['image_sha256'],
            publication_sha256=item['publication_sha256'], tools=tools, files=files,
            science_case=str((task / 'case').relative_to(project)), case_files=case_files,
            science=references, distance_angstrom=fixture['distance_angstrom'], gpu=gpu_cases,
            lammps_features=features, help_body_styles=sorted(body[0].split()),
            binary_sha256=binary['sha256'], tolerances=science.TOLERANCES,
            gpu_tolerances=gpu_proof['tolerances'])
        print(partition + ': accepted inputs, numeric references and full style registry pinned', flush=True)
    destination = deployment.PACKAGE / 'native-reference.json'
    with destination.open('x') as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write('\n')


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    main()
