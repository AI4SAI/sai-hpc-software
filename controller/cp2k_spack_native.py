#!/usr/bin/env python3
"""Offline native resolver evidence; fetch strictly from the prepared mirror."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from cp2k_spack import validate_cache
from cp2k_spack_environment import environment

WHEELS = {
    'clingo-5.7.1-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl':
        '11f3c784139ed56e0ec9c4cf1e090c839998d23704014a248d2a52da83471def',
    'cffi-2.1.1-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl':
        'c1453022f490d2459a11819d83ad1d586e9ff65a12ac3e705ffebd46d3685dcf',
    'pycparser-3.0-py3-none-any.whl':
        'b727414169a36b7d524c1c3e31839a521725078d7b2ff038656844266160a992',
}
SYSTEM_PACKAGES = {
    'make': '4.3', 'pkgconf': '1.8.1', 'python3': '3.12.3',
    'zlib1g-dev': '1.3', 'libgsl-dev': '2.7.1', 'autoconf': '2.71',
    'automake': '1.16.5', 'libtool': '2.4.7', 'm4': '1.4.19',
    'perl': '5.38.2', 'libboost1.83-dev': '1.83.0', 'libgmp-dev': '6.3.0',
}


def output(argv):
    try:
        return subprocess.check_output(argv, text=True, stderr=subprocess.STDOUT).strip()
    except subprocess.CalledProcessError as error:
        print(error.output, file=sys.stderr, flush=True)
        raise


def write_result(name, data):
    with (Path('/results') / name).open('x') as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write('\n')


def upstream_debian_version(version):
    """Drop Debian epoch/revision and dfsg repack suffix, not upstream digits."""
    upstream = version.split(':')[-1].split('-')[0]
    return re.sub(r'[.+]dfsg\d*$', '', upstream)


def prepare(partition):
    validate_cache(Path('/input/spack'), partition, require_archives=True)
    wheel_root = Path('/input/spack/sources/solver-wheels')
    if {p.name for p in wheel_root.iterdir()} != set(WHEELS):
        raise ValueError('unexpected offline solver wheel set')
    for name, digest in WHEELS.items():
        if hashlib.sha256((wheel_root / name).read_bytes()).hexdigest() != digest:
            raise ValueError('offline solver checksum mismatch: ' + name)
    versions = {name: output(['dpkg-query', '-W', '-f=${Version}', name])
                for name in SYSTEM_PACKAGES}
    profile = {
        'partition': partition, 'cpu': output(['lscpu']),
        'python': sys.version, 'system_packages': versions,
        'gcc': output(['/opt/devtools/gcc/13.3.0/bin/gcc', '-dumpfullversion']),
        'native_flags': output(['/opt/devtools/gcc/13.3.0/bin/gcc', '-march=native',
                                '-mtune=native', '-Q', '--help=target']),
        'mpi': os.environ.get('MPI_HOME'), 'blas': os.environ.get('OPENBLAS_ROOT'),
        'ld_library_path': os.environ.get('LD_LIBRARY_PATH'),
    }
    write_result('native-profile.json', profile)
    for name, expected in SYSTEM_PACKAGES.items():
        actual = upstream_debian_version(versions[name])
        if actual != expected:
            raise ValueError(f'node external {name} is {actual}, expected {expected}')
    if profile['gcc'] != '13.3.0' or sys.version_info[:2] != (3, 12):
        raise ValueError('unexpected compiler or offline solver Python ABI')
    isa = 'avx2' if partition == '16V100' else 'avx512'
    for name in ('mpi', 'blas'):
        if not profile[name] or not profile[name].endswith('-' + isa):
            raise ValueError('node module ISA mismatch: ' + name)


def fetch(partition, target, native_os):
    import spack.environment as ev
    import spack.spec
    from spack.compilers.libraries import CompilerPropertyDetector

    env = ev.active_environment()
    if env is None:
        raise ValueError('activate the native environment first')
    prefix = '/opt/software/cp2k/development/spack-native-probe/' + partition
    expected = environment(partition, prefix, target, native_os)['spack']
    roots = list(env.concrete_roots())
    constraints = {s.split('@')[0]: s for s in expected['specs']}
    if {s.name for s in roots} != set(constraints):
        raise ValueError('native dependency roots changed')
    for spec in roots:
        if not spec.satisfies(spack.spec.Spec(constraints[spec.name])):
            raise ValueError('native root feature mismatch: ' + str(spec))
    externals = {name: item['externals'][0] for name, item in expected['packages'].items()
                 if 'externals' in item}
    # Spack discovers the compiler's native libc itself. It is not a borrowed
    # application dependency: permit exactly the observed node ABI and prefix.
    gcc = next(s for s in env.all_specs() if s.name == 'gcc')
    libc = CompilerPropertyDetector(gcc).default_libc()
    if libc is not None:
        observed_libc = output(['getconf', 'GNU_LIBC_VERSION']).split()
        if (libc.name != 'glibc' or libc.external_path not in ('/', '/usr') or
                observed_libc != ['glibc', str(libc.version)]):
            raise ValueError('compiler libc does not match the node runtime')
        externals['glibc'] = {'spec': 'glibc@=' + str(libc.version),
                              'prefix': libc.external_path}
    nodes = []
    for spec in env.all_specs():
        if spec.name == 'cp2k' or '/opt/apps/' in str(spec.external_path or ''):
            raise ValueError('old CP2K dependency contamination')
        if spec.external:
            allowed = externals.get(spec.name)
            if (not allowed or spec.external_path != allowed['prefix'] or
                    not spec.satisfies(spack.spec.Spec(allowed['spec']))):
                raise ValueError('unapproved external: ' + str(spec))
        elif str(spec.architecture.target) != target or str(spec.architecture.os) != native_os:
            raise ValueError('non-native dependency: ' + str(spec))
        if spec.name == 'tiled-mm' and partition == '16V100' and not spec.satisfies('+cuda cuda_arch=70'):
            raise ValueError('incorrect tiled-mm CUDA backend')
        nodes.append({'spec': str(spec), 'hash': spec.dag_hash(),
                      'external': bool(spec.external), 'external_path': spec.external_path,
                      'target': str(spec.architecture.target)})
    write_result('validated-graph.json', {'partition': partition, 'target': target,
                                         'os': native_os, 'nodes': nodes})
    fetched = []
    for spec in env.all_specs():
        if spec.external or not spec.package.has_code:
            continue
        print('MIRROR_ONLY_FETCH', spec, flush=True)
        spec.package.do_fetch(mirror_only=True)
        fetched.append(spec.dag_hash())
    write_result('probe-success.json', {'partition': partition, 'target': target,
                                       'os': native_os, 'mirror_only': True,
                                       'fetched': fetched, 'installed': False})


if __name__ == '__main__':
    if sys.argv[1] == 'prepare':
        prepare(sys.argv[2])
    elif sys.argv[1] == 'fetch':
        fetch(*sys.argv[2:])
    else:
        raise SystemExit('unknown native probe command')
