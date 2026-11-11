#!/usr/bin/env python3
"""Freeze a successful SIF build into the public administrator handoff."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

PUBLIC = Path('/home/stardust/sai-hpc-software/deployment/gpumdkit-1.5.9')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    if destination.exists():
        if digest(destination) != digest(source):
            raise ValueError('different public file already exists: ' + str(destination))
    else:
        shutil.copyfile(source, destination)
    destination.chmod(0o444)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--job', required=True)
    args = parser.parse_args()
    run = args.run.resolve()
    if not run.is_relative_to('/org/org-open-sai/proj-sai-hpc-software/experimental/gpumdkit/runs'):
        raise ValueError('unexpected build location')
    source = run.parents[1] / 'cache/source/source.tar.gz'
    if digest(source) != '536fa538cf0afc80db4197d87bbf292279337e24de4399698f6ec87b4b70ab6b':
        raise ValueError('source archive changed')
    inputs = json.loads((PUBLIC / 'inputs.lock.json').read_text())
    for name, pinned in inputs.items():
        if digest(Path(name)) != pinned['sha256']:
            raise ValueError('dependency package changed: ' + name)
    accounting = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', args.job,
        '-o', 'JobIDRaw,State,ExitCode,Partition,Account,WorkDir,NodeList'], text=True)
    row, = [line.split('|') for line in accounting.splitlines() if line.split('|')[0] == args.job]
    if row[1:3] != ['COMPLETED', '0:0'] or row[4] != 'proj-sai-hpc-software' or row[5] != str(run):
        raise ValueError('build not successfully completed: ' + accounting)
    validation = run / ('validation-' + args.job)
    proof = json.loads((validation / 'runtime/sif-acceptance/acceptance.json').read_text())
    image_sha = digest(run / 'gpumdkit-1.5.9.sif')
    if (proof['passed'] is not True or proof['route'] != 'sif'
            or proof['image_sha256'] != image_sha or proof['job'] != args.job):
        raise ValueError('missing SIF functional acceptance')
    for name, checksum in proof['files'].items():
        if (Path(name).is_absolute() or '..' in Path(name).parts
                or digest(validation / 'runtime/sif-acceptance' / name) != checksum):
            raise ValueError('raw science evidence changed')
    native = json.loads((validation / 'runtime/native-acceptance/acceptance.json').read_text())
    if (native['passed'] is not True or native['route'] != 'staged-native'
            or native['image_sha256'] != image_sha or native['job'] != args.job):
        raise ValueError('missing staged native functional acceptance')
    for name, checksum in native['files'].items():
        if digest(validation / 'runtime/native-acceptance' / name) != checksum:
            raise ValueError('raw native evidence changed')
    for source, destination in [(run / 'gpumdkit-1.5.9.sif', PUBLIC / 'gpumdkit-1.5.9.sif'),
            (run / 'inventory.json', PUBLIC / 'inventory.json'),
            (validation / 'runtime/sif-acceptance/acceptance.json', PUBLIC / 'acceptance.json'),
            (validation / 'runtime/native-acceptance/acceptance.json', PUBLIC / 'native-acceptance.json'),
            (run / 'runtime/help.txt', PUBLIC / 'build-info/help.txt'),
            (run / 'runtime/doctor.txt', PUBLIC / 'build-info/doctor.txt'),
            (run / 'runtime/requirements.lock', PUBLIC / 'build-info/requirements.lock')]:
        copy(source, destination)
    inventory = json.loads((PUBLIC / 'inventory.json').read_text())
    count = sum(row['kind'] == 'file' for row in inventory)
    size = sum(row.get('size', 0) for row in inventory)
    (PUBLIC / 'build-info/index.txt').write_text(
        'GPUMDkit v1.5.9; source 073371a45c9f58b0c0e2ca9ba7a7934705c6a677\n'
        'Upstream: https://github.com/zhyan0603/GPUMDkit/releases/tag/v1.5.9\n'
        'Installation: /opt/apps/GPUMDkit/1.5.9\n'
        'One x86_64 installation. Python 3.12.13; generic manylinux wheels.\n'
        'Calorine/NepTrain extensions: GCC 14.3.0; -march=x86-64 -mtune=generic; no partition-specific native flags.\n'
        'Python, package libraries and standard data are included. GPUMD/NEP executables remain separate.\n'
        'Features: POSCAR/CIF/LAMMPS/DFT data conversion; dataset filtering/sampling; Calorine/NepTrain CPU NEP;\n'
        'structure and thermal/diffusion analysis; headless plotting; CP2K/VASP/ABACUS job preparation.\n'
        'Optional functions not included: OVITO, DeepMD/DPA prediction, deprecated PyNEP, ferrodispcalc, NepTrainKit GUI.\n'
        'Upstream v1.5.9 has an unchanged 1.5.8 CLI banner; the release tag/SHA above is authoritative.\n'
        f'Payload: {count} files, {size} bytes. Build/acceptance job: {args.job}, {row[3]}, {row[6]}.\n'
        'Acceptance: POSCAR and compressed LAMMPS data conversion, periodic distances,\n'
        'NEP4 energy/force/virial against pinned labels, two-worker prediction, PNG plotting.\n'
        'Complete help: cat ' + str(PUBLIC / 'build-info/help.txt') + '\n'
        'Dependency availability: cat ' + str(PUBLIC / 'build-info/doctor.txt') + '\n'
        'Pinned package versions: cat ' + str(PUBLIC / 'build-info/requirements.lock') + '\n')
    image = PUBLIC / 'gpumdkit-1.5.9.sif'
    metadata = {name: digest(PUBLIC / name) for name in ('modulefile', 'inputs.lock.json', 'inventory.json', 'acceptance.json', 'native-acceptance.json',
                'build-info/index.txt', 'build-info/help.txt', 'build-info/doctor.txt', 'build-info/requirements.lock')}
    release = dict(schema=1, version='1.5.9', source_sha='073371a45c9f58b0c0e2ca9ba7a7934705c6a677',
                   prefix='/opt/apps/GPUMDkit/1.5.9', image_sha256=digest(image), image_bytes=image.stat().st_size,
                   files=count, payload_bytes=size, metadata=metadata, build=str(run), accounting=row,
                   controls={p.name: digest(p) for p in (run / 'control').iterdir() if p.is_file()},
                   validation_controls={p.name: digest(p) for p in (validation / 'control').iterdir() if p.is_file()},
                   source_archive_sha256='536fa538cf0afc80db4197d87bbf292279337e24de4399698f6ec87b4b70ab6b')
    (PUBLIC / 'release.json').write_text(json.dumps(release, indent=2) + '\n')
    for path in [PUBLIC, *PUBLIC.rglob('*')]:
        if path.is_dir():
            path.chmod(0o755)
        else:
            path.chmod(0o444 if path.suffix in ('.json','.sif','.txt','.lock') else 0o644)
    print(json.dumps(release, indent=2))


if __name__ == '__main__':
    main()
