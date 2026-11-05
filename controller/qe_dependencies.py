"""Stage QE's exact Git submodules before entering the networkless builder."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

from remote_controller import safe_sha

REPOSITORIES = {
    'devxlib': 'https://gitlab.com/max-centre/components/devicexlib.git',
    'fox': 'https://github.com/pietrodelugas/fox.git',
    'mbd': 'https://github.com/libmbd/libmbd.git',
    'wannier90': 'https://github.com/wannier-developers/wannier90.git',
}
HDF5_SHA256 = 'e4defbac30f50d64e1556374aa49e574417c9e72c6b1de7a4ff88c4b1bea6e9b'


def git(repository, *arguments):
    return subprocess.check_output(['git', '-C', str(repository), '-c',
                                    'core.hooksPath=/dev/null', *arguments], text=True).strip()


def pins(repository, commit):
    commit = safe_sha(commit)
    records = dict(line.split()[::-1] for line in
                   git(repository, 'show', commit + ':external/submodule_commit_hash_records').splitlines())
    result = {}
    for name, url in REPOSITORIES.items():
        mode, kind, sha, path = git(repository, 'ls-tree', commit, 'external/' + name).split()
        if (mode, kind, path) != ('160000', 'commit', 'external/' + name) or records[name] != sha:
            raise ValueError('QE gitlink and recorded submodule disagree: ' + name)
        result[name] = dict(sha=safe_sha(sha), url=url)
    return result


def prepare(repository, commit, directory):
    """Keep shallow Git metadata so upstream CMake can validate each gitlink offline."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    locked = pins(repository, commit)
    tree = directory / 'tree'
    tree.mkdir()

    def fetch(item):
        name, row = item
        path = tree / 'external' / name
        path.mkdir(parents=True)
        git(path, 'init', '-q')
        git(path, 'fetch', '--depth=1', '--no-tags', row['url'], row['sha'])
        git(path, 'checkout', '--detach', 'FETCH_HEAD')
        if git(path, 'rev-parse', 'HEAD') != row['sha']:
            raise ValueError('submodule checkout changed')
        shutil.rmtree(path / '.git/hooks')
        return name

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(fetch, locked.items()))
    archive = directory / ('qe-submodules-' + commit + '.tar.gz')
    with tarfile.open(archive, 'w:gz') as stream:
        stream.add(tree / 'external', arcname='external')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = directory / 'qe-submodules.json'
    manifest.write_text(json.dumps(dict(schema=1, source_sha=commit, submodules=locked,
                                       archive=archive.name, sha256=digest), sort_keys=True) + '\n')
    return [(archive, digest), (manifest, hashlib.sha256(manifest.read_bytes()).hexdigest())]


def cache_archives(directory, commit):
    directory = Path(directory)
    directory.mkdir(parents=True)
    repository = directory / 'source'
    repository.mkdir()
    git(repository, 'init', '-q')
    git(repository, 'fetch', '--depth=1', '--no-tags', 'https://github.com/QEF/q-e.git', safe_sha(commit))
    archives = prepare(repository, commit, directory / 'dependencies')
    archive = directory / 'hdf5-1.14.6.tar.gz'
    url = 'https://github.com/HDFGroup/hdf5/releases/download/hdf5_1.14.6/' + archive.name
    with urllib.request.urlopen(url, timeout=120) as source, archive.open('xb') as output:
        shutil.copyfileobj(source, output)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != HDF5_SHA256:
        raise ValueError('HDF5 release archive checksum changed')
    return [*archives, (archive, HDF5_SHA256)]


def populate(repository, directory):
    repository, directory = Path(repository), Path(directory)
    manifest = json.loads((directory / 'qe-submodules.json').read_text())
    commit = git(repository, 'rev-parse', 'HEAD')
    if (manifest['schema'] != 1 or manifest['source_sha'] != commit or
            manifest['submodules'] != pins(repository, commit) or
            manifest['archive'] != 'qe-submodules-' + commit + '.tar.gz'):
        raise ValueError('offline submodules differ from the pinned QE source')
    archive = directory / manifest['archive']
    if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest['sha256']:
        raise ValueError('offline submodule archive changed')
    with tarfile.open(archive) as stream:
        for member in stream.getmembers():
            path = repository / member.name
            if (not path.resolve().is_relative_to(repository.resolve()) or
                    not member.name.startswith('external/') or
                    not (member.isdir() or member.isfile() or member.issym())):
                if member.name == 'external' and member.isdir():
                    continue
                raise ValueError('unsafe submodule archive member')
            if member.issym() and not (path.parent / member.linkname).resolve().is_relative_to(repository.resolve()):
                raise ValueError('escaping submodule link')
        stream.extractall(repository, filter='data')
    for name, row in manifest['submodules'].items():
        if git(repository / 'external' / name, 'rev-parse', 'HEAD') != row['sha']:
            raise ValueError('extracted submodule identity changed')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('prepare', 'populate'))
    parser.add_argument('repository', type=Path)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--commit')
    args = parser.parse_args()
    if args.operation == 'prepare':
        print(json.dumps([(str(path), sha) for path, sha in prepare(args.repository, args.commit, args.directory)]))
    else:
        print(json.dumps(populate(args.repository, args.directory)))
