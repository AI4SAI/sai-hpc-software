#!/usr/bin/env python3
"""Prepare a readable administrator handoff from completed library SIFs."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

CONTROL = Path(__file__).resolve().parents[2] / 'controller'
sys.path.insert(0, str(CONTROL))
from cp2k_devtools import TARGETS
from cp2k_devtools_stage import accepted, accounting
from devtools_install import checksum, verify, require, plain, read_release, restore_extracted_modes
from remote_controller import safe_name


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')
    path.chmod(0o644)


def wait_for_builds(project, runs, *, timeout=18000):
    """Observe existing allocations only; never resubmit, cancel or install."""
    active = {'PENDING', 'RUNNING', 'COMPLETING', 'CONFIGURING', 'SUSPENDED', 'REQUEUED'}
    deadline, previous = time.monotonic() + timeout, None
    while time.monotonic() < deadline:
        states = {run: accounting(Path(project) / 'runs' / safe_name(run)) for run in runs}
        if states != previous:
            print(json.dumps(states, sort_keys=True), flush=True)
            previous = states
        for run, (job, state) in states.items():
            require(not state or state[0] in active or state == ['COMPLETED', '0:0'],
                    f'Library build {run} ({job}) failed: {state}; evidence is preserved')
        if all(state == ['COMPLETED', '0:0'] for _, state in states.values()):
            return
        time.sleep(30)
    raise TimeoutError('Library build observation timed out; allocations were not cancelled')


def admin_handoff(bundle, output):
    """Copy four compressed SIFs and small manifests to the admin-readable home."""
    output = plain(output)
    require(output.parent == Path('/home/stardust/sai-hpc-software/deployment'), 'Unexpected administrator handoff parent')
    require(not output.exists(), 'Administrator handoff already exists')
    release = read_release(bundle)
    output.mkdir(mode=0o755)
    for name in ('install.py', 'install.sh', 'README.md'):
        shutil.copyfile(bundle / name, output / name)
    shutil.copytree(bundle / 'manifests', output / 'manifests')
    (output / 'images').mkdir(mode=0o755)
    for partition, build in release['builds'].items():
        source = plain(Path(build['sif']))
        require(checksum(source) == build['sif_sha256'], 'Accepted SIF changed before handoff')
        image = output / 'images' / (partition + '.sif')
        shutil.copyfile(source, image)
        require(checksum(image) == build['sif_sha256'], 'SIF handoff copy changed')
        image.chmod(0o444)
    for row in release['packages']:
        partition = Path(row.pop('payload')).parts[1]
        row.pop('inventory')
        row.update(image='images/' + partition + '.sif', image_sha256=release['builds'][partition]['sif_sha256'])
    release['format'] = 'sif'
    write_json(output / 'release.json', release)
    (output / 'release.sha256').write_text(checksum(output / 'release.json') + '\n')
    for parent, dirs, files in os.walk(output):
        Path(parent).chmod(0o755)
        for name in files:
            path = Path(parent) / name
            path.chmod(0o444 if path.suffix == '.sif' else 0o644)
    print('ADMIN_HANDOFF_READY ' + str(output), flush=True)


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
            restore_extracted_modes(source, entries)
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
        request = json.loads((run / 'request.json').read_text())
        if 'source_build' in request:
            release['builds'][partition]['source_build'] = request['source_build']
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
    parser.add_argument('--wait', action='store_true', help='Observe submitted jobs, then prepare the handoff automatically')
    parser.add_argument('--admin-output', type=Path, help='Optional SIF-only handoff under the existing home deployment directory')
    args = parser.parse_args()
    if args.wait:
        wait_for_builds(args.project, args.run)
    prepare(args.project, args.run, args.output, allow_partial=args.allow_partial)
    if args.admin_output:
        admin_handoff(args.output, args.admin_output)
