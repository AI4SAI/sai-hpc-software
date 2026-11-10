#!/usr/bin/env python3
"""Publish reviewed captures as small, immutable login-readable text records."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import sys


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--captures', required=True, type=Path)
    parser.add_argument('--public', required=True, type=Path)
    parser.add_argument('--controller', required=True, type=Path)
    parser.add_argument('--qe-release', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.controller))
    sys.path.insert(0, str(args.captures))
    from build_info import qe_configuration, render
    descriptions = read(args.captures / 'descriptions.json')
    public = args.public
    records, entries = {}, {}

    def save(path, text):
        if path.resolve() != path:
            raise ValueError('redirected documentation path: ' + str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text() != text:
            raise ValueError('immutable documentation changed: ' + str(path))
        if not path.exists():
            path.write_text(text)
        path.chmod(0o444)
        for parent in path.parents:
            if parent == public:
                break
            parent.chmod(0o755)
        records[str(path)] = sha(path)

    def index(module, destination, paths, summary):
        path = destination / 'index.txt'
        text = [summary, 'Read these files directly on login nodes; no module load is required.', '']
        text += [part + ': cat ' + str(file) for part, file in paths]
        save(path, '\n'.join(text) + '\n')
        entries[module] = str(path)

    for description in descriptions:
        module = '/'.join(description['module'].split('/')[1:]).removesuffix('.lua')
        destination = public / 'module-info/20261111' / module
        paths = []
        for part in ('4V100', '16V100', '8V100V0', 'DSPRHBM'):
            name = module.replace('/', '__') + '.json'
            if module == 'elpa/2026.02.001-2603-gnu-auto' and part == '8V100V0':
                # Both module branches select the identical avx2/nvidia prefix.
                # Keep the real capture partition/job; do not relabel a 16V100 run.
                left = read(args.captures / part / name)['files']
                right = read(args.captures / '16V100-supplement' / name)['files']
                if left != right:
                    raise ValueError('shared ELPA prefix/configuration differs')
                paths.append(('8V100V0 (same installed prefix as 16V100)', destination / '16V100.txt'))
                continue
            source = args.captures / (part + '-supplement') / name
            if not source.exists():
                source = args.captures / part / name
            if not source.exists():
                continue
            value = read(source)
            sections = []
            for command in value['commands']:
                # These programs do not support the guessed option. Retain the
                # raw diagnostic under operations, not as a false build record.
                if command['command'] == ['phonopy', '--version'] or module.startswith('qe/'):
                    continue
                if command['returncode'] and module.startswith('cp2k/'):
                    continue
                sections += ['$ ' + shlex.join(command['command']),
                             'Exit status: ' + str(command['returncode']), command['output']]
            for file in value['files']:
                if 'text' in file:
                    sections += ['Recorded file: ' + file['path'], 'SHA256: ' + file['sha256'], file['text']]
            if module.startswith('qe/'):
                release = next(row for row in read(args.qe_release)['deliveries'] if row['partition'] == part)
                row = dict(identity=release['identity'], image=release['origin_image'])
                for record in qe_configuration(row):
                    sections += ['Recorded file: ' + record['source'], 'SHA256: ' + record['sha256'], record['output']]
            if not sections:
                continue
            header = dict(module=module, partition=part, job=value['job'], node=value['node'],
                          captured=value['captured'], capture_path=str(source), capture_sha256=sha(source),
                          modulefiles=value['modulefiles'])
            path = destination / (part + '.txt')
            save(path, description['summary'] + '\nFeatures: ' + description['features'] + '\n'
                 + json.dumps(header, indent=2) + '\n\n' + '\n\n'.join(sections) + '\n')
            paths.append((part, path))
        if not paths:
            raise ValueError('no static information: ' + module)
        index(module, destination, paths, description['summary'])

    root = public / 'sif-beta'
    for software in ('abacus', 'cp2k', 'lammps', 'deepmd-kit', 'qe', 'gpumd'):
        branch = 'develop' if software in ('abacus', 'lammps', 'qe') else 'master'
        module = software + '/' + branch + '-beta-later'
        wrapper = public / 'modulefiles/apps' / module
        generation = re.search(r'set sai_beta_generation "([A-Za-z0-9_.-]+)"', wrapper.read_text())[1]
        manifest = read(root / 'generations' / generation / 'manifest.json')
        catalog = Path(manifest['bundle']) / 'catalog.json'
        if sha(catalog) != manifest['catalog_sha256']:
            raise ValueError('changed public beta catalog')
        snapshot = read(catalog)
        destination = root / 'build-info' / generation / software
        paths = []
        for part, row in snapshot['applications'][software]['partitions'].items():
            if software in ('qe', 'gpumd'):
                work = Path(manifest['validated_catalog']).parent.parent / part
                request = read(work / 'request.json')
                original = read(Path(request['catalog']))['applications'][software]['partitions'][part]
                if original['sha256'] != row['sha256'] or original['identity'] != row['identity']:
                    raise ValueError('accepted output belongs to another image')
                source = work / 'case/stdout.txt'
                sections = [dict(source=str(source), sha256=sha(source), output=source.read_text())]
                job = (work / 'job.id').read_text().strip()
                if software == 'qe':
                    sections += qe_configuration(row)
            else:
                source = args.captures / part / (module.replace('/', '__') + '.json')
                value = read(source)
                loaded = read(source.with_suffix('.load.json'))
                if 'commit=' + row['identity']['source_sha'][:12] not in loaded['output']:
                    raise ValueError('captured beta differs from public generation')
                if any(c['returncode'] for c in value['commands']):
                    raise ValueError('failed standard build information command')
                sections, job = value['commands'], value['job']
            value = dict(identity=row['identity'], image_sha256=row['sha256'], job=job,
                         source=str(source), source_sha256=sha(source), records=sections)
            path = destination / (part + '.txt')
            save(path, render(value))
            paths.append((part, path))
        index(module, destination, paths, module + '; generation=' + generation)
    args.output.write_text(json.dumps(dict(entries=entries, files=records), indent=2) + '\n')
    print(str(len(entries)) + ' modules; ' + str(len(records)) + ' static text files')


if __name__ == '__main__':
    main()
