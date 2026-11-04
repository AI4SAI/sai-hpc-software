#!/usr/bin/env python3
"""Prepare a readable administrator handoff from completed library SIFs."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

CONTROL = Path(__file__).resolve().parents[2] / 'controller'
sys.path.insert(0, str(CONTROL))
from cp2k_devtools import TARGETS
from cp2k_devtools_stage import accepted
from devtools_install import checksum, verify, require, plain


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')
    path.chmod(0o644)


def prepare(project, runs, output, *, allow_partial=False):
    project, output = plain(project), plain(output)
    require(output.is_relative_to(project / 'deployment'), 'Handoff must stay in project deployment storage')
    require(not output.exists(), 'Use a fresh handoff directory')
    providers = [(run, accepted(project, run)) for run in runs]
    partitions = [provider['install']['partition'] for _, provider in providers]
    require(len(set(partitions)) == len(partitions), 'Duplicate partition')
    require(allow_partial or set(partitions) == set(TARGETS), 'All four partitions are required')
    output.mkdir(parents=True, mode=0o755)
    package = Path(__file__).resolve().parent
    shutil.copyfile(CONTROL / 'devtools_install.py', output / 'install.py')
    for name in ('install.sh', 'README.md'):
        shutil.copyfile(package / name, output / name)
    release = dict(schema=1, purpose='cp2k-spack-devtools', partitions=partitions, packages=[], builds={})
    candidate = dict(schema=1, partitions={})
    for run_id, provider in providers:
        run = project / 'runs' / run_id
        partition = provider['install']['partition']
        evidence = output / 'evidence' / partition
        evidence.mkdir(parents=True)
        for name in ('provider.json', 'spack.lock', 'validated-graph.json', 'libint-export-repair.json',
                     'native-profile.json', 'image-driver.json', 'overlay-sync-dependencies.json', 'fsck.log'):
            shutil.copyfile(run / 'results' / name, evidence / name)
        shutil.copytree(run / 'results/sif-consumer', evidence / 'sif-consumer')
        for name in ('request.json', 'job.id', 'job.sbatch'):
            shutil.copyfile(run / name, evidence / name)
        for path in (run / 'results').glob('managed-overlay-*'):
            shutil.copyfile(path, evidence / path.name)
        candidate['partitions'][partition] = dict(provider=provider,
            native_flags=json.loads((evidence / 'native-profile.json').read_text())['native_flags'],
            libint_repair=json.loads((evidence / 'libint-export-repair.json').read_text()))
        squash = output / ('.' + partition + '.squashfs')
        with squash.open('xb') as stream:
            subprocess.run(['/opt/devtools/apptainer/1.4.4/bin/apptainer', 'sif', 'dump', '1',
                            str(run / 'libraries.sif')], stdout=stream, check=True)
        extraction = output / ('.' + partition + '.extract')
        subprocess.run(['unsquashfs', '-no-progress', '-no-xattrs', '-processors', '2', '-d', str(extraction),
                        str(squash), *[p.lstrip('/') for p in provider['packages']]], check=True)
        nodes = {n['prefix']: n for n in provider['install']['nodes'] if not n['external']}
        for prefix, entries in provider['packages'].items():
            node = nodes[prefix]
            source = extraction / prefix.lstrip('/')
            verify(source, entries)
            payload = output / 'payload' / partition / node['name']
            payload.parent.mkdir(parents=True, exist_ok=True)
            source.rename(payload)
            manifest = output / 'manifests' / partition / (node['name'] + '.json')
            write_json(manifest, entries)
            release['packages'].append(dict(name=node['name'], version=node['version'], prefix=prefix,
                payload=str(payload.relative_to(output)), manifest=str(manifest.relative_to(output)),
                manifest_sha256=checksum(manifest), dag_hash=node['hash']))
        release['builds'][partition] = dict(run_id=run_id, job_id=(run / 'job.id').read_text().strip(),
            sif=str(run / 'libraries.sif'), sif_sha256=checksum(run / 'libraries.sif'),
            contract_sha256=provider['contract_sha256'], library_consumers_verified=True,
            cp2k_scientific_verified=False, published=False)
        # Remove only this invocation's generated extraction scratch.
        shutil.rmtree(extraction)
        squash.unlink()
    write_json(output / 'release.json', release)
    write_json(output / 'site-spack-candidate.json', candidate)
    (output / 'release.sha256').write_text(checksum(output / 'release.json') + '\n')
    for parent, dirs, files in os.walk(output):
        Path(parent).chmod(0o755)
        # Preserve executable payload files; make reports/admin scripts readable.
        if not Path(parent).is_relative_to(output / 'payload'):
            for name in files:
                (Path(parent) / name).chmod(0o644)
    print(output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--run', action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--allow-partial', action='store_true', help='Explicit single-partition pilot handoff')
    args = parser.parse_args()
    prepare(args.project, args.run, args.output, allow_partial=args.allow_partial)
