#!/usr/bin/env python3
"""Independent Spack library identity, full development payload and consumers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from cp2k_spack import SPACK_COMMIT, PACKAGES_COMMIT, GCC_PREFIX
from cp2k_spack_environment import environment
from cp2k_spack_link import ROOTS, check_files
from cp2k_spack_store import DEVTOOLS, validate_package_prefix, patch_id
from devtools_install import LIBRARIES, inventory, verify, require, plain

TARGETS = {'16V100': '16v100-avx2', '4V100': '4v100-avx512',
           '8V100V0': '8v100v0-avx512', 'DSPRHBM': 'dsprhbm'}


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def contract(partition, target, native_os):
    env = environment(partition, DEVTOOLS, target, native_os, layout='devtools')['spack']
    # No CP2K commit, source version, run directory or build parallelism here.
    return dict(schema=1, partition=partition, target=target, os=native_os,
                spack_commit=SPACK_COMMIT, packages_commit=PACKAGES_COMMIT,
                libint_repair=patch_id(), specs=env['specs'], packages=env['packages'],
                install_tree=env['config']['install_tree'], concretizer=env['concretizer'])


def validate_install(data):
    require(data.get('layout') == 'devtools' and data.get('install_prefix') == DEVTOOLS,
            'Not an independent devtools build')
    require(data.get('partition') in TARGETS and data.get('dependencies_installed') is True
            and data.get('cp2k_built') is False and data.get('published') is False, 'Invalid dependency evidence')
    roots, runtime = data['roots'], data['runtime_prefixes']
    require(set(roots) == ROOTS and runtime and len(runtime) == len(set(runtime)), 'Incomplete dependency closure')
    nodes = {node['prefix']: node for node in data['nodes'] if not node['external']}
    require(len(nodes) == sum(not node['external'] for node in data['nodes']), 'Duplicate dependency paths')
    require(set(runtime) <= nodes.keys() and set(roots.values()) <= set(runtime), 'Unproven runtime closure')
    selected = {nodes[path]['hash'] for path in runtime}
    external = {node['hash'] for node in data['nodes'] if node['external']}
    require(len({node['hash'] for node in data['nodes']}) == len(data['nodes']), 'Duplicate dependency hashes')
    for path in runtime:
        node = nodes[path]
        require(node['name'] in LIBRARIES, 'Unreviewed link/run dependency: ' + node['name'])
        validate_package_prefix(path, node['name'], node['version'], node['target'], node['hash'])
        require(set(node['dependencies']) <= selected | external, 'Transitive dependency omitted from payload')
    for name, path in roots.items():
        require(nodes[path]['name'] == name, 'Root package mismatch')
    return [nodes[path] for path in sorted(runtime)]


def validate_provider(data, *, files=False):
    require(data.get('schema') == 1 and data.get('kind') == 'spack-devtools', 'Wrong provider schema')
    installed = validate_install(data['install'])
    c = data['contract']
    require(c == contract(c['partition'], c['target'], c['os']) and data['contract_sha256'] == digest(c),
            'Shared dependency versions, ABI or recipe changed')
    require(data['install']['partition'] == c['partition'], 'Provider partition mismatch')
    require(all(node['target'] == c['target'] for node in installed), 'Provider native architecture mismatch')
    expected_versions = {spec.split('@')[0]: spec.split('@')[1].split()[0] for spec in c['specs']}
    for node in installed:
        if node['name'] in expected_versions:
            require(node['version'] == expected_versions[node['name']], 'Root library version differs from contract')
    packages = data['packages']
    require(set(packages) == {node['prefix'] for node in installed}, 'Provider file inventory is incomplete')
    if files:
        for prefix, entries in packages.items():
            verify(Path(prefix), entries)
        check_files(data['install']['roots'])
    return installed


def stage(results=Path('/results'), export=Path('/workspace/export')):
    data = json.loads((results / 'install-success.json').read_text())
    nodes = validate_install(data)
    graph = json.loads((results / 'validated-graph.json').read_text())
    require(graph['partition'] == data['partition'] and graph['install_prefix'] == DEVTOOLS,
            'Native graph differs from the installed store')
    expected = contract(data['partition'], graph['target'], graph['os'])
    # Keep all normal package content including headers, modules, archives and
    # CMake/pkg-config metadata. Only Spack's own build logs/DB are excluded.
    packages = {}
    for node in nodes:
        source = plain(Path(node['prefix']))
        destination = export / source.relative_to('/')
        require(source.is_dir() and not destination.exists(), 'Invalid payload source/destination')
        shutil.copytree(source, destination, symlinks=True,
                        ignore=lambda directory, names: ['.spack'] if Path(directory) == source else [])
        for parent, dirs, files in os.walk(destination):
            Path(parent).chmod(0o755)
            for name in files:
                path = Path(parent) / name
                if not path.is_symlink():
                    path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
        packages[str(source)] = inventory(destination)
    record = dict(schema=1, kind='spack-devtools', contract=expected,
                  contract_sha256=digest(expected), install=data, packages=packages,
                  scientific_verified=False, published=False)
    validate_provider(record)
    with (results / 'provider.json').open('x') as stream:
        json.dump(record, stream, sort_keys=True)
        stream.write('\n')
    return record


def run(argv, **kwargs):
    print('+ ' + ' '.join(map(str, argv)), flush=True)
    return subprocess.run(list(map(str, argv)), check=True, **kwargs)


def verify_libraries(provider, output):
    data = json.loads(Path(provider).read_text())
    validate_provider(data, files=True)
    output = plain(output)
    output.mkdir(parents=True, exist_ok=False)
    roots = data['install']['roots']
    prefixes = data['install']['runtime_prefixes']
    env = dict(os.environ)
    env['LD_LIBRARY_PATH'] = ':'.join(p + '/' + d for p in prefixes for d in ('lib', 'lib64')) + ':' + env.get('LD_LIBRARY_PATH', '')
    dynamic = []
    for prefix, entries in data['packages'].items():
        for entry in entries:
            if entry['kind'] != 'file':
                continue
            path = Path(prefix) / entry['path']
            with path.open('rb') as stream:
                elf = stream.read(4) == b'\x7fELF'
            if not elf:
                continue
            result = run(['readelf', '-d', path], capture_output=True, text=True)
            for line in result.stdout.splitlines():
                if '(RPATH)' in line or '(RUNPATH)' in line:
                    require(not any(p in line for p in ('/workspace', '/home/', '/tmp/', '/opt/apps/')),
                            'Transient RPATH: ' + str(path))
            if '(NEEDED)' in result.stdout:
                linked = run(['ldd', path], capture_output=True, text=True, env=env).stdout
                require('not found' not in linked, 'Missing runtime library: ' + str(path))
                require(not any(p in linked for p in ('/workspace/', '/opt/apps/')), 'Borrowed runtime library')
                dynamic.append({'path': str(path), 'ldd': linked, 'dynamic': result.stdout})
    (output / 'linkage.json').write_text(json.dumps(dynamic, indent=2) + '\n')
    require(dynamic, 'No dynamic libraries verified')
    # Compile and execute an MPI/Fortran HDF5 consumer using shipped headers,
    # modules and static archives; import Libint's actual Fortran module too.
    smoke = Path(__file__).with_name('cp2k_devtools_smoke.f90')
    hdf5 = Path(roots['hdf5'])
    libdir = next((hdf5 / name for name in ('lib', 'lib64') if (hdf5 / name / 'libhdf5.a').is_file()), None)
    require(libdir is not None, 'Missing static parallel HDF5')
    mods = sorted(Path(roots['libint']).rglob('libint_f.mod'))
    require(len(mods) == 1, 'Missing or ambiguous Libint Fortran module')
    binary = output / 'hdf5-consumer'
    env.update(OMPI_FC=GCC_PREFIX + '/bin/gfortran', OMPI_CC=GCC_PREFIX + '/bin/gcc',
               OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
    # HDF5's CMake installation keeps the Fortran-to-C bridge in a separate
    # archive. Static consumers need it after the Fortran interface archive.
    run([env['MPI_HOME'] + '/bin/mpifort', '-I' + str(hdf5 / 'include'), '-I' + str(mods[0].parent),
         smoke, libdir / 'libhdf5_fortran.a', libdir / 'libhdf5_f90cstub.a',
         libdir / 'libhdf5.a', '-lz', '-ldl', '-lm', '-o', binary], env=env)
    result = run([env['MPI_HOME'] + '/bin/mpiexec', '--allow-run-as-root', '--bind-to', 'none', '-n', '2', binary],
                 env=env, cwd=output, capture_output=True, text=True)
    (output / 'hdf5-consumer.log').write_text(result.stdout + result.stderr)
    require('SAI_HDF5_MPI_ROUNDTRIP_PASS' in result.stdout, 'Parallel HDF5 consumer did not pass')
    # PLUMED must load its independent kernel, not merely print a version.
    result = run([Path(roots['plumed']) / 'bin/plumed', 'info', '--version'], env=env,
                 capture_output=True, text=True)
    (output / 'plumed.log').write_text(result.stdout + result.stderr)
    (output / 'passed.json').write_text(json.dumps(dict(contract_sha256=data['contract_sha256'],
        verified_libraries=len(dynamic), hdf5_mpi_ranks=2, cp2k_scientific_verified=False)) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='op', required=True)
    sub.add_parser('stage')
    check = sub.add_parser('verify')
    check.add_argument('provider', type=Path)
    check.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.op == 'stage':
        stage()
    else:
        verify_libraries(args.provider, args.output)


if __name__ == '__main__':
    main()
