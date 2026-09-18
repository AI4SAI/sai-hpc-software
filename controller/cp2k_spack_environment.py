#!/usr/bin/env python3
"""Spack environment for replacing the borrowed CP2K toolchain dependencies.

CP2K/DBCSR/TBLITE remain in the existing native build, including cuSOLVERMp
and the standalone ELPA module. This environment does NOT use Spack's CP2K
recipe (which has a different feature contract). Generate on the target node
with its observed Spack CPU/OS, then concretize and mirror before installation.
"""
import argparse
from copy import deepcopy
from pathlib import Path
import re

from cp2k_spack import TARGETS, config, write_config

ROOT_SPECS = (
    'libint@2.6.0 +fortran +shared tune=cp2k-lmax-5',
    'libxsmm@2.0.0 +shared build_system=cmake',
    'libvori@220621 +pic',
    'hdf5@1.14.6 +mpi +fortran ~hl ~cxx ~shared',
    'plumed@2.10.1 +mpi +shared +gsl optional_modules=all',
)
HPCX_ROOT = '/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/comm_libs/12.9/hpcx/hpcx-2.25.1'
MPI_RUNTIME_PATH = HPCX_ROOT + '/hcoll/lib:' + HPCX_ROOT + '/sharp/lib'


def external(spec, prefix, **attributes):
    entry = {'spec': spec, 'prefix': prefix}
    if attributes:
        entry['extra_attributes'] = attributes
    return {'buildable': False, 'externals': [entry]}


def environment(partition, install_prefix, native_target, native_os, jobs=8):
    """Return JSON-compatible spack.yaml content, with no expanded host store."""
    for value in (native_target, native_os):
        if not re.fullmatch(r'[a-z][a-z0-9_.-]*', value):
            raise ValueError('expected an observed Spack architecture identifier')
    if native_target in ('native', 'x86_64', 'x86_64_v2', 'x86_64_v3', 'x86_64_v4'):
        raise ValueError('record the native CPU microarchitecture, not a generic ISA')
    if not 1 <= jobs <= 64:
        raise ValueError('invalid build parallelism')
    manifest = config(partition, Path('/input/spack'), install_prefix=Path(install_prefix))
    isa = TARGETS[partition]['isa']
    gcc = '/opt/devtools/gcc/13.3.0'
    blas = '/opt/devtools/saiblas/2603-gnu-' + isa
    mpi = '/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-' + isa
    packages = {
        'all': {'require': ['target=' + native_target, 'os=' + native_os],
                'providers': {'mpi': ['openmpi'], 'blas': ['openblas'], 'lapack': ['openblas'],
                              'scalapack': ['netlib-scalapack'], 'zlib-api': ['zlib']}},
        'c': {'require': 'gcc@13.3.0'},
        'cxx': {'require': 'gcc@13.3.0'},
        'fortran': {'require': 'gcc@13.3.0'},
        'gcc': external('gcc@13.3.0 languages=c,c++,fortran os=' + native_os, gcc,
                        compilers={name: gcc + '/bin/' + exe for name, exe in
                                   [('c', 'gcc'), ('cxx', 'g++'), ('fortran', 'gfortran')]},
                        flags={name: '-O3 -march=native -mtune=native' for name in
                               ('cflags', 'cxxflags', 'fflags')}),
        # Site libmpi needs HCOLL, whose baked-in /opt/mellanox RUNPATH is not
        # the standalone HPCX installation. Preserve the module's exact paths
        # in Spack's clean build environment, and bind them into the lock.
        'openmpi': external('openmpi@5.0.10 +cuda +fortran fabrics=ucx', mpi,
                            environment={'set': {'OPAL_PREFIX': mpi, 'PMIX_INSTALL_PREFIX': mpi},
                                         'prepend_path': {'LD_LIBRARY_PATH': MPI_RUNTIME_PATH}}),
        'openblas': external('openblas@0.3.32 threads=openmp', blas),
        'netlib-scalapack': external('netlib-scalapack@2.2.3', blas),
        'cuda': external('cuda@12.9.1', '/opt/devtools/nvidia/cuda-12.9.1'),
        'cmake': external('cmake@3.31.6', '/opt/devtools/cmake/3.31.6'),
        'gmake': external('gmake@4.3', '/usr'),
        'pkgconf': external('pkgconf@1.8.1', '/usr'),
        'python': external('python@3.12.3', '/usr'),
        'zlib': external('zlib@1.3', '/usr'),
        'gsl': external('gsl@2.7.1', '/usr'),
        'autoconf': external('autoconf@2.71', '/usr'),
        'automake': external('automake@1.16.5', '/usr'),
        'libtool': external('libtool@2.4.7', '/usr'),
        'm4': external('m4@1.4.19', '/usr'),
        'perl': external('perl@5.38.2', '/usr'),
        'boost': external('boost@1.83.0', '/usr'),
        'gmp': external('gmp@6.3.0 +cxx', '/usr'),
    }
    if TARGETS[partition]['cuda']:
        packages['tiled-mm'] = {'require': 'cuda_arch=70'}
    backend = '+cuda ~rocm' if TARGETS[partition]['cuda'] else '~cuda ~rocm'
    specs = [*ROOT_SPECS, 'cosma@2.7.0 +shared ' + backend,
             'spla@1.6.1 +fortran ~static ' + backend]
    settings = deepcopy(manifest['container']['config'])
    settings.update(build_jobs=jobs, checksum=True, connect_timeout=10,
                    misc_cache='/workspace/spack-misc-cache', test_stage='/workspace/spack-tests')
    return {'spack': {
        'specs': specs, 'view': False,
        'repos': manifest['container']['repos'],
        'packages': packages,
        'bootstrap': {'enable': False},
        'concretizer': {'unify': True, 'reuse': False, 'compiler_mixing': False,
                        'targets': {'host_compatible': True}, 'timeout': 120},
        'config': settings,
        # Override the public mirrors; compute must only see the prepared cache.
        # Binary reuse remains disabled until partition/ABI/cache validation.
        'mirrors:': {'sai-sources': {'url': 'file:///input/spack/sources',
                                    'source': True, 'binary': False}},
    }}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('partition', choices=sorted(TARGETS))
    parser.add_argument('--install-prefix', required=True)
    parser.add_argument('--native-target', required=True)
    parser.add_argument('--native-os', required=True)
    parser.add_argument('--jobs', type=int, default=8)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    write_config(args.output, environment(args.partition, args.install_prefix,
                                          args.native_target, args.native_os, args.jobs))


if __name__ == '__main__':
    main()
