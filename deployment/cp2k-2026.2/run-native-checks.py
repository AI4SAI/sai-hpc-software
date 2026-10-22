#!/usr/bin/env python3
"""Repeat accepted one/two-node cases with physical CP2K and installed data.

Uses the original frozen acceptance parser and the actual accepted SIF result
for this partition. No comparison to the retired 2026.1 installation occurs.
References are read-only; results live under the executing user's home work tree.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

sys.dont_write_bytecode = True
import install as deployment


def run(project, output, work_root):
    deployment.require(output.is_relative_to(work_root / 'runtime-tests'),
                       'native check results must be inside the writable home work directory')
    partition = os.environ['SLURM_JOB_PARTITION']
    release = json.loads((deployment.PACKAGE / 'release.json').read_text())
    spec = next(item for item in release['deliveries'] if item['partition'] == partition)
    prefix = deployment.APP_ROOT / ('cp2k-release-' + spec['build_id']) / partition
    deployment.require(os.environ['SAI_CP2K_PREFIX'] == str(prefix), 'loaded the wrong CP2K module')
    receipt = json.loads((prefix / deployment.RECEIPT).read_text())
    deployment.require(receipt['image_sha256'] == spec['image_sha256'] and
                       receipt['source_native_manifest_sha256'] == spec['manifest_sha256'],
                       'installed runtime differs from the accepted artifact')
    deployment.verify_tree(prefix, receipt['files'], installed=True, receipt=receipt)
    deployment.shared_dependencies()
    provider_path = prefix / 'share/sai/site-elpa.json'
    elpa_prefix = json.loads(provider_path.read_text())['prefix'] if provider_path.exists() else None
    sidecar = (project / 'containers/software/cp2k/release' / spec['build_id'] /
               partition / (spec['run_id'] + '.json'))
    accepted = json.loads(sidecar.read_text())
    proof = accepted['cp2k_science']
    control = Path(accepted['controller'])
    deployment.require(deployment.checksum(control / 'cp2k_acceptance.py') == proof['controller_sha256'],
                       'frozen scientific parser changed')
    deployment.require(deployment.checksum(control / 'cp2k_benchmark.py') == proof['helpers_sha256'],
                       'frozen scientific helper changed')
    sys.path.insert(0, str(control))
    import cp2k_acceptance as science
    evidence_path = project / 'runtime-tests' / proof['run_id'] / 'results/evidence.json'
    deployment.require(deployment.checksum(evidence_path) == proof['evidence_sha256'],
                       'accepted SIF scientific evidence changed')
    evidence = json.loads(evidence_path.read_text())
    deployment.require(evidence['artifact_sha256'] == spec['image_sha256'], 'wrong scientific reference')
    hosts = (output / 'nodes.txt').read_text().splitlines()
    deployment.require(len(hosts) == 2 and len(set(hosts)) == 2, 'two distinct compute nodes required')
    mapping = science.mpi_mapping(receipt['identity']['target'])
    deployment.require(os.environ['MAP_OPT'].lower() == mapping.lower(), 'site MPI mapping differs')
    binary = prefix / 'bin/cp2k.psmp'
    wrapper = deployment.PACKAGE / 'native-rank.sh'
    samples, comparisons, placements = {}, {}, {}

    def execute(folder, nodes, arguments):
        folder.mkdir(parents=True, exist_ok=True)
        trace = folder / 'ranks'
        trace.mkdir()
        environment = {**os.environ, 'SAI_NATIVE_TRACE': str(trace)}
        command = ['mpirun', '-np', str(nodes), '--host', ','.join(hosts[:nodes]),
                   '--map-by', mapping, '--report-bindings', 'bash', str(wrapper), *arguments]
        started = time.monotonic()
        with (folder / 'cp2k.log').open('x') as log:
            subprocess.run(command, check=True, cwd=folder, env=environment,
                           stdout=log, stderr=subprocess.STDOUT)
        elapsed = time.monotonic() - started
        (folder / 'elapsed.txt').write_text(str(elapsed) + '\n')
        ranks = [(trace / f'rank-{rank}.tsv').read_text().strip().split('\t') for rank in range(nodes)]
        deployment.require([row[0] for row in ranks] == [str(rank) for rank in range(nodes)]
                           and [row[1] for row in ranks] == hosts[:nodes]
                           and all(row[2] == str(binary) for row in ranks), 'native rank placement differs')
        return elapsed, ranks

    version_dir = output / 'version'
    _, version_ranks = execute(version_dir, 2, ['--version'])
    version = science.fixtures.parse_version((version_dir / 'cp2k.log').read_text())
    deployment.require(version == evidence['versions']['candidate'],
                       'native CP2K version/revision/features differ from the accepted SIF')
    for rank in range(2):
        paths = list((version_dir / 'ranks').glob(f'loader-{rank}.*'))
        deployment.require(len(paths) == 1, 'missing actual loader trace')
        loaded = re.findall(r'calling init:\s*(\S+)', paths[0].read_text(errors='replace'))
        deployment.require(any('/hpcx/hpcx-2.25.1/hcoll/lib/libhcoll.so.1' in path for path in loaded)
                           and not any('/opt/mellanox/hcoll/' in path for path in loaded),
                           'HCOLL did not load from the real HPCX directory')
        if elpa_prefix:
            elpa_loaded = [path for path in loaded if Path(path).name.startswith('libelpa')]
            deployment.require(elpa_loaded and all(path.startswith(elpa_prefix + '/lib/') for path in elpa_loaded),
                               'ELPA did not load from the accepted shared installation')
    for name, digest in science.DATA_HASHES.items():
        deployment.require(deployment.checksum(prefix / 'share/cp2k/data' / name) == digest,
                           'installed basis/potential data differ from the accepted fixtures')
    for case, mode in science.schedule():
        key = case + '/' + mode
        folder = output / 'cases' / case / mode
        folder.parent.mkdir(parents=True, exist_ok=True)
        # The original inputs are used verbatim. Do not copy their local basis
        # files: the physical CP2K must find its own packaged data directory.
        original = evidence_path.parent.parent / 'cases' / case / mode / 'input.inp'
        data = original.read_bytes()
        deployment.require(hashlib.sha256(data).hexdigest() == evidence['input_hashes'][case]['input.inp'],
                           'accepted calculation input changed')
        folder.mkdir()
        (folder / 'input.inp').write_bytes(data)
        elapsed, ranks = execute(folder, science.MODES[mode], ['-i', 'input.inp'])
        sample = science.parse_science((folder / 'cp2k.log').read_text(),
                                      (folder / 'forces.xyz').read_text(), str(elapsed), case)
        comparisons[key] = science.compare_science(sample, evidence['samples'][key])
        samples[key], placements[key] = sample, ranks
        print(key + ': native energy/forces agree with accepted SIF', flush=True)
    single_multi = {case: science.compare_science(samples[case + '/single'], samples[case + '/multi'])
                    for case in science.CASES}
    result = {'schema': 1, 'passed': True, 'scope': 'physical native installation; single/two-node science',
              'identity': receipt['identity'], 'artifact_sha256': spec['image_sha256'],
              'job': os.environ['SLURM_JOB_ID'], 'native_version': version,
              'reference_evidence_sha256': proof['evidence_sha256'], 'version_ranks': version_ranks,
              'samples': samples, 'comparisons_to_sif': comparisons,
              'single_multi_comparisons': single_multi, 'placements': placements}
    (output / 'native-acceptance.json').write_bytes(deployment.canonical(result))
    print('CP2K_PHYSICAL_NATIVE_ACCEPTANCE_PASSED', flush=True)


if __name__ == '__main__':
    run(Path(sys.argv[1]).resolve(strict=True), Path(sys.argv[2]).resolve(strict=True),
        Path(sys.argv[3]).resolve(strict=True))
