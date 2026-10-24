#!/usr/bin/env python3
"""Install a fixed, accepted SIF snapshot as independent Lmod applications."""
import argparse
import copy
import json
from pathlib import Path
import re
import shlex
import shutil

from module_publication import tcl
from release_contract import validate_identity
from sif_runtime import PROGRAMS, bind_path, digest, file_identity, require


def validate_snapshot(snapshot):
    require(snapshot.get('schema') == 1 and re.fullmatch('[0-9]{8}', snapshot.get('snapshot', '')),
            'invalid snapshot date/schema')
    require(bool(snapshot.get('applications')) and not set(snapshot['applications']) - set(PROGRAMS),
            'unknown or empty SIF application catalog')
    for name, app in snapshot['applications'].items():
        require(re.fullmatch('[A-Za-z0-9_.-]+', app['version']), 'invalid module version')
        require(bool(app['partitions']), 'empty partition matrix')
        sources = set()
        for partition, row in app['partitions'].items():
            identity = validate_identity(row['identity'])
            require(identity['software'] == name and identity['partition'] == partition,
                    'snapshot software/partition differs from accepted identity')
            sources.add((identity['track'], identity['source_sha']))
            bind_path(row['image'])
            require(re.fullmatch('[0-9a-f]{64}', row['sha256']), 'invalid SIF checksum')
            require(row['build_verified'] is True and row['scientific_verified'] is True,
                    'module catalog requires build and scientific acceptance')
            require(row['evidence'] and re.fullmatch('[0-9a-f]{64}', row['evidence_sha256']),
                    'missing acceptance evidence binding')
            for dependency in row['binds']:
                bind_path(dependency)
                require(dependency.startswith('/opt/apps/')
                        and not Path(identity['install_prefix']).is_relative_to(dependency),
                        'dependency bind would hide the application')
            if 'environment' in row:
                import hashlib
                require(hashlib.sha256(row['environment'].encode()).hexdigest() == row['environment_sha256'],
                        'runtime environment differs from acceptance')
        require(len(sources) == 1, 'one displayed version must use one source across all partitions')
    return snapshot


def render_module(root, software, app):
    stem = 'SAI_SIF_' + software.upper().replace('-', '_')
    partitions = ' '.join(app['partitions'])
    identity = next(iter(app['partitions'].values()))['identity']
    description = (f"{software} {app['version']}; accepted SIF; partitions: {partitions}; "
                   f"source: {identity['source_sha']}")
    lines = ['#%Module1.0', 'module-whatis ' + tcl(description),
             'proc ModulesHelp {} {',
             '    puts stderr ' + tcl(description + '\nLoad inside the allocation. Set TMPDIR to a home/project job directory before host mpirun. '
                                      'GPUMD and dp are direct commands; deepmd-python selects this Python package.'),
             '}', f'conflict {software}', f'conflict {software}-sif',
             'set sai_partition ""',
             'if {[module-info mode remove]} {',
             f'    if {{[info exists env({stem}_PARTITION)]}} {{set sai_partition $env({stem}_PARTITION)}}',
             '} elseif {[module-info mode load]} {',
             '    if {![info exists env(SLURM_JOB_PARTITION)]} {error "load the SIF module inside the Slurm allocation"}',
             '    set sai_partition $env(SLURM_JOB_PARTITION)',
             f'    if {{[lsearch -exact {{{partitions}}} $sai_partition] < 0}} {{error "no accepted SIF for this partition"}}',
             '}', 'if {$sai_partition ne ""} {',
             '    prepend-path MODULEPATH /opt/modules/modulefiles/devtools',
             '    module load apptainer/1.4.4']
    if software == 'cp2k':
        lines += ['    module load fftw/3.3.10 saiblas/2603-gnu-auto',
                  '    if {$sai_partition ne "DSPRHBM"} {module load cuda/12.9.1 nvmplibs/26.7-tmp}']
    elif software in ('lammps', 'deepmd-kit'):
        lines.append('    module load cuda/12.9.1')
    if software != 'gpumd':
        lines.append('    module load openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto')
    lines += [f'    setenv {stem}_PARTITION $sai_partition', '    switch -- $sai_partition {']
    for partition, row in app['partitions'].items():
        lines += [f'        {partition} {{',
                  f'            setenv {stem}_IMAGE {tcl(row["image"])}',
                  f'            setenv {stem}_PREFIX {tcl(row["identity"]["install_prefix"])}', '        }']
    lines += ['    }', '}', f'setenv {stem}_SOURCE {tcl(identity["source_sha"])}',
              f'prepend-path PATH {tcl(root / "bin" / software)}', '']
    return '\n'.join(lines)


def write(path, content, mode=0o444):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        stream.write(content)
    path.chmod(mode)


def verify_sources(snapshot):
    """Full hash at publication; runtime only needs the per-job image cache."""
    for name, app in snapshot['applications'].items():
        for partition, row in app['partitions'].items():
            image = Path(row['image'])
            before = file_identity(image)
            require(digest(image) == row['sha256'] and file_identity(image) == before,
                    f'{name}/{partition}: accepted SIF changed')
            print(f'{name}/{partition}: verified {before[2]} bytes', flush=True)


def install(snapshot, destination, *, copy_images=False, module_root=None):
    snapshot = copy.deepcopy(validate_snapshot(snapshot))
    destination = bind_path(destination)
    require(str(destination).startswith(('/home/', '/org/', '/opt/'))
            and destination.resolve() == destination and not destination.exists(),
            'destination must be a new real home/project/opt directory')
    require(copy_images or not str(destination).startswith('/opt/'),
            'public opt installation must copy images out of private build trees')
    runtime_source = Path(__file__).with_name('sif_runtime.py')
    runtime_sha256 = digest(runtime_source)
    require(snapshot.get('runtime_sha256', runtime_sha256) == runtime_sha256,
            'installer runtime differs from the prepared/tested bundle')
    snapshot['runtime_sha256'] = runtime_sha256
    if module_root is not None:
        module_root = bind_path(module_root)
        require(module_root.resolve() == module_root, 'redirected public module root')
        for name, app in snapshot['applications'].items():
            require(not (module_root / (name + '-sif') / app['version']).exists(),
                    'public module version already exists; do not overwrite it')
    verify_sources(snapshot)
    destination.mkdir(parents=True, mode=0o755)
    runtime = destination / 'libexec/sif_runtime.py'
    write(runtime, runtime_source.read_text(), 0o555)
    for name, app in snapshot['applications'].items():
        for partition, row in app['partitions'].items():
            if copy_images:
                image = destination / 'images' / name / (partition + '.sif')
                image.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(row['image'], image)
                image.chmod(0o444)
                require(digest(image) == row['sha256'], 'copied SIF checksum changed')
                row['source_image'] = row['image']
                row['image'] = str(image)
            if 'environment' in row:
                row['environment_file'] = f'environments/{name}-{partition}.sh'
                write(destination / row['environment_file'], row.pop('environment'))
        module = destination / 'modulefiles' / (name + '-sif') / app['version']
        write(module, render_module(destination, name, app))
        # Each wrapper pins this catalog directly. Loading another application
        # cannot change its image/environment through a shared SAI_* variable.
        for program in PROGRAMS[name]:
            argv = ['/usr/bin/python3', '-I', '-B', str(runtime), str(destination / 'catalog.json'), name, program]
            write(destination / 'bin' / name / program,
                  '#!/usr/bin/env bash\nexec ' + shlex.join(argv) + ' "$@"\n', 0o555)
    write(destination / 'catalog.json', json.dumps(snapshot, indent=2, sort_keys=True) + '\n')
    # Public deployment copies only runtime files. No build trees, science
    # fixtures or acceptance scripts are part of this payload.
    for path in destination.rglob('*'):
        if path.is_dir():
            path.chmod(0o755)
    if module_root is not None:
        for name, app in snapshot['applications'].items():
            module = destination / 'modulefiles' / (name + '-sif') / app['version']
            public_module = module_root / (name + '-sif') / app['version']
            write(public_module, module.read_text())
            public_module.parent.chmod(0o755)
    print('module use ' + str(destination / 'modulefiles'), flush=True)
    return snapshot


def load_snapshot(path):
    snapshot = json.loads(path.read_text())
    # A prepared handoff has readable SIF copies and a tested runtime. The
    # administrator need not traverse any original private build directory.
    for app in snapshot['applications'].values():
        for row in app['partitions'].values():
            if 'environment_file' in row:
                source = path.parent / row.pop('environment_file')
                require(source.is_relative_to(path.parent) and source.resolve() == source,
                        'redirected prepared environment')
                require(digest(source) == row['environment_sha256'], 'prepared environment changed')
                row['environment'] = source.read_text()
    return validate_snapshot(snapshot)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--copy-images', action='store_true')
    parser.add_argument('--module-root', type=Path, help='also install the five public Lmod entries here')
    args = parser.parse_args()
    snapshot = load_snapshot(args.snapshot)
    if args.destination is None:
        verify_sources(snapshot)
    else:
        install(snapshot, args.destination, copy_images=args.copy_images, module_root=args.module_root)


if __name__ == '__main__':
    main()
