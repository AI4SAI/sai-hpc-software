#!/usr/bin/env python3
"""Read-only administrator preflight of the current home-maintained beta payload."""
import hashlib
import json
from pathlib import Path
import re
import shlex
import stat

PUBLIC_HOME = Path('/home/stardust/sai-hpc-software')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify(path, expected, *, executable=False):
    require(path.resolve() == path and path.is_file(), 'redirected or missing payload: ' + str(path))
    mode = path.stat().st_mode
    require(stat.S_ISREG(mode) and not mode & 0o222 and mode & 0o004,
            'payload must be immutable and publicly readable: ' + str(path))
    if executable:
        require(mode & 0o001, 'launcher must be publicly executable: ' + str(path))
    require(digest(path) == expected, 'payload checksum changed: ' + str(path))


def main():
    checked = set()
    programs = {'abacus': ('abacus',), 'cp2k': ('cp2k', 'cp2k.psmp'), 'lammps': ('lmp',),
                'deepmd-kit': ('dp', 'deepmd-python'), 'gpumd': ('gpumd', 'nep', 'gnep')}
    for name, commands in programs.items():
        branch = 'develop' if name in ('abacus', 'lammps') else 'master'
        alias = PUBLIC_HOME / 'modulefiles/apps' / name / (branch + '-beta-later')
        require(alias.is_symlink(), 'missing beta symlink: ' + str(alias))
        fixed = alias.resolve(strict=True)
        require(fixed.parent == alias.parent and not alias.readlink().is_absolute(), 'redirected beta alias')
        match = re.search(r'^set sai_beta_generation "([A-Za-z0-9_.-]+)"$', fixed.read_text(), re.M)
        require(match, 'invalid fixed beta module')
        generation = PUBLIC_HOME / 'sif-beta/generations' / match[1]
        manifest = json.loads((generation / 'manifest.json').read_text())
        require(manifest['schema'] == 2 and name in manifest['software'], 'invalid beta generation')
        require(manifest['versions'][name]['name'] == fixed.name, 'fixed module name changed')
        verify(fixed, manifest['versions'][name]['sha256'])
        for relative, expected in manifest['files'].items():
            path = generation / relative
            require(path.is_relative_to(generation) and '..' not in path.parts, 'invalid generation path')
            verify(path, expected)
        bundle = Path(manifest['bundle'])
        verify(bundle / 'catalog.json', manifest['catalog_sha256'])
        verify(bundle / 'libexec/sif_runtime.py', manifest['runtime_sha256'], executable=True)
        catalog = json.loads((bundle / 'catalog.json').read_text())
        require('memory_bootstrap' in catalog, 'automatic memory runtime has not been published')
        bootstrap = catalog['memory_bootstrap']
        verify(Path(bootstrap['image']), bootstrap['sha256'])
        reports = {r['partition']: r for r in json.loads((generation / 'acceptance.json').read_text())}
        for partition, row in catalog['applications'][name]['partitions'].items():
            report = reports[partition]
            require(report.get('passed') is True and report.get('automatic_memory') is True
                    and name in report and report['runtime_sha256'] == manifest['runtime_sha256']
                    and report['catalog_sha256'] == manifest['validated_catalog_sha256'],
                    'missing matching private-memory execution acceptance')
            image = Path(row['image'])
            if image not in checked:
                verify(image, row['sha256'])
                checked.add(image)
            if 'environment_file' in row:
                verify(bundle / row['environment_file'], row['environment_sha256'])
        launchers = [(name, command) for command in commands]
        launchers += [('mpi', command) for command in ('mpirun', 'mpiexec', 'prted')]
        for folder, command in launchers:
            argv = ['/usr/bin/python3', '-I', '-B', str(bundle / 'libexec/sif_runtime.py'),
                    str(bundle / 'catalog.json'), '_mpi' if folder == 'mpi' else name, command]
            expected = '#!/usr/bin/env bash\nexec ' + shlex.join(argv) + ' "$@"\n'
            verify(bundle / 'bin' / folder / command, hashlib.sha256(expected.encode()).hexdigest(), executable=True)
        print(f'{name}: {fixed.name}; runtime, images and launchers verified', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit('Beta preflight: ' + str(error))
