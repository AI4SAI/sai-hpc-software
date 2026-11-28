"""Prepare exact sources on the relay; compute jobs consume checked archives."""
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import sys
from urllib.request import urlopen

from source_cache import checksum, git
from source_transport import source_repository
from spingnn_config import site


def prepare(directory, source):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    settings = site()
    prior = directory / 'sources.json'
    if prior.is_file():
        record = json.loads(prior.read_text())
        if (record.get('schema') == 2 and record.get('source_sha') == source['source_sha'] and
                record.get('requirements_sha256') == requirements_digest() and
                all((directory / name).is_file() and checksum(directory / name) == digest
                    for name, digest in record.get('files', {}).items()) and 'wheels.tar.gz' in record['files']):
            return [(directory / name, checksum(directory / name)) for name in (*record['files'], 'sources.json')]
    with source_repository('https://github.com/Hyyu-Lab/SpinGNN.git', source['source_sha'],
                           directory, 'spingnn2') as repository:
        with (directory / 'spingnn2.tar.gz').open('wb') as stream:
            subprocess.run(['git', '-C', str(repository), 'archive', '--format=tar.gz',
                            '--prefix=spingnn2/', source['source_sha']], stdout=stream, check=True)
    # The upstream overlay checks both the annotated tag object and its commit.
    # Keep that small, shallow Git database, not an unversioned source tarball.
    with tempfile.TemporaryDirectory(dir=directory) as work:
        repository = Path(work) / 'lammps-upstream'
        subprocess.run(['git', 'init', '--bare', str(repository)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(repository), '-c', 'fetch.fsckObjects=true', 'fetch',
                        '--depth=1', 'https://github.com/lammps/lammps.git',
                        'refs/tags/' + settings['lammps_tag'] + ':refs/tags/' + settings['lammps_tag']],
                       check=True, timeout=900)
        if (git(repository, 'rev-parse', 'refs/tags/' + settings['lammps_tag']) != settings['lammps_tag_object']
                or git(repository, 'rev-parse', settings['lammps_tag'] + '^{commit}') != settings['lammps_sha']):
            raise ValueError('upstream LAMMPS tag or commit changed')
        with tarfile.open(directory / 'lammps.tar.gz', 'w:gz') as archive:
            archive.add(repository, arcname='lammps-upstream')
    for item in settings['archives']:
        path = directory / item['name']
        if not path.exists() or checksum(path) != item['sha256']:
            temporary = path.with_suffix('.download')
            with urlopen(item['url'], timeout=120) as response, temporary.open('wb') as output:
                shutil.copyfileobj(response, output)
            if checksum(temporary) != item['sha256']:
                raise ValueError('dependency archive checksum mismatch: ' + item['name'])
            temporary.replace(path)
    wheels = directory / 'wheels'
    wheels.mkdir(exist_ok=True)
    control = Path(__file__).parent
    subprocess.run([sys.executable, '-m', 'pip', 'download', '--only-binary=:all:',
                    '--python-version', '3.12', '--implementation', 'cp', '--abi', 'cp312',
                    '--platform', 'manylinux_2_28_x86_64', '--platform', 'manylinux2014_x86_64',
                    '--dest', str(wheels), '-r', str(control / 'spingnn_runtime.txt'),
                    '-r', str(control / 'spingnn_wheels.txt'), '--no-deps'], check=True, timeout=900)
    # Download transitive requirements without torch: export uses the pinned
    # site's existing torch environment, not a second multi-GB runtime.
    subprocess.run([sys.executable, '-m', 'pip', 'download', '--only-binary=:all:',
                    '--python-version', '3.12', '--implementation', 'cp', '--abi', 'cp312',
                    '--platform', 'manylinux_2_28_x86_64', '--platform', 'manylinux2014_x86_64',
                    '--dest', str(wheels), '-r', str(control / 'spingnn_runtime.txt'), 'pytest'],
                   check=True, timeout=900)
    with tarfile.open(directory / 'wheels.tar.gz', 'w:gz') as archive:
        archive.add(wheels, arcname='wheels')
    names = ['spingnn2.tar.gz', 'lammps.tar.gz', 'wheels.tar.gz', *[item['name'] for item in settings['archives']]]
    manifest = dict(schema=2, source_sha=source['source_sha'], requirements_sha256=requirements_digest(),
                    files={name: checksum(directory / name) for name in names})
    (directory / 'sources.json').write_text(json.dumps(manifest, sort_keys=True) + '\n')
    return [(directory / name, checksum(directory / name)) for name in (*names, 'sources.json')]


def requirements_digest():
    import hashlib
    control = Path(__file__).parent
    return hashlib.sha256((control / 'spingnn_runtime.txt').read_bytes() +
                          (control / 'spingnn_wheels.txt').read_bytes()).hexdigest()


def unpack(directory, expected_sha):
    directory = Path(directory)
    inputs = directory / 'input/spingnn-dependencies'
    manifest = json.loads((inputs / 'sources.json').read_text())
    expected = {'spingnn2.tar.gz', 'lammps.tar.gz', 'wheels.tar.gz', *[item['name'] for item in site()['archives']]}
    if (manifest.get('schema') != 2 or manifest.get('source_sha') != expected_sha
            or manifest.get('requirements_sha256') != requirements_digest()
            or set(manifest.get('files', {})) != expected):
        raise ValueError('source manifest differs from the frozen release')
    for item in site()['archives']:
        if manifest['files'][item['name']] != item['sha256']:
            raise ValueError('dependency differs from the reviewed source pin')
    destination = directory / 'sources'
    destination.mkdir()
    for name, digest in manifest['files'].items():
        path = inputs / name
        if path.is_symlink() or checksum(path) != digest:
            raise ValueError('input archive changed: ' + name)
        with tarfile.open(path) as archive:
            for member in archive.getmembers():
                target = destination / member.name
                if (not (member.isfile() or member.isdir()) or Path(member.name).is_absolute()
                        or '..' in Path(member.name).parts or target.resolve() != target):
                    raise ValueError('unsafe source archive member')
            archive.extractall(destination)
    return manifest
