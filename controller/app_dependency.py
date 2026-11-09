#!/usr/bin/env python3
"""Build and qualify the application-private HDF5 and LibTorch providers."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

from abacus_dependencies import extract_archive, load_lock
from devtools_install import APP_LIBRARY_PREFIXES, checksum, inventory, require, verify
from qe_dependencies import HDF5_SHA256

CONTROL = Path(__file__).resolve().parent
ROOTS = {name: Path(prefix) for name, prefix in APP_LIBRARY_PREFIXES.items()}
RESULTS = Path('/results')


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def run(argv, *, cwd=None, env=None):
    result = subprocess.run([str(x) for x in argv], cwd=cwd, env=env,
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(str(argv) + '\n' + result.stdout)
    return result.stdout


def tool(name):
    path = Path(shutil.which(name)).resolve(strict=True)
    return dict(path=str(path), sha256=checksum(path))


def record_hdf5():
    source = Path('/workspace/receipts/share/sai')
    target = RESULTS / 'hdf5-build'
    shutil.copytree(source, target)
    write(target / 'toolchain.json', dict(source_sha256=HDF5_SHA256,
        compiler=run(['nvfortran', '--version']), mpi=run(['mpifort', '--showme']),
        tools={name: tool(name) for name in ('nvfortran', 'nvc', 'mpifort', 'mpicc')},
        mpi_home=os.environ['MPI_HOME'], cpu=run(['lscpu']),
        flags='-O3 -tp=host', mpi_interface='F90', mpi_f08=False))
    # Keep the custom SZIP finder as normal CMake development metadata.
    config = ROOTS['qe-hdf5'] / 'lib/cmake/qe_szip'
    config.mkdir(parents=True)
    shutil.copyfile(CONTROL / 'qe_szipConfig.cmake', config / 'qe_szipConfig.cmake')


def prepare_torch():
    locked = next(item for item in load_lock()['archives'] if item['name'] == 'LibTorch')
    archive = Path('/input/abacus-dependencies') / locked['file']
    require(checksum(archive) == locked['sha256'], 'LibTorch archive changed')
    workspace = Path('/workspace/torch-source')
    workspace.mkdir()
    extract_archive(archive, workspace, locked['root'])
    prefix = ROOTS['abacus-libtorch']
    prefix.parent.mkdir(parents=True)
    (workspace / locked['root']).rename(prefix)
    license_dir = prefix / 'share/licenses/pytorch'
    license_dir.mkdir(parents=True)
    shutil.copyfile(CONTROL / 'app_dependency_torch.LICENSE', license_dir / 'LICENSE')
    subprocess.run(['chmod', '-R', 'a+rX,u+w,go-w', str(prefix)], check=True)
    config = (prefix / 'share/cmake/Torch/TorchConfig.cmake').read_text()
    require('_GLIBCXX_USE_CXX11_ABI=1' in config, 'LibTorch C++ ABI changed')
    require(not (prefix / 'lib/libtorch_cuda.so').exists(), 'Expected the CPU distribution')
    write(RESULTS / 'torch-source.json', dict(archive=locked, prefix=str(prefix),
        license_url='https://raw.githubusercontent.com/pytorch/pytorch/v2.1.2/LICENSE',
        license_sha256=checksum(license_dir / 'LICENSE'), source_compilation=False))


def export():
    packages = {}
    for name, prefix in ROOTS.items():
        entries = inventory(prefix)
        packages[name] = dict(prefix=str(prefix), files=entries,
                              version='1.14.6' if name == 'qe-hdf5' else '2.1.2')
        target = Path('/workspace/export') / prefix.relative_to('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(prefix, target, symlinks=True)
        verify(target, entries)
    write(RESULTS / 'provider.json', dict(schema=1, purpose='application-dependencies',
        packages=packages, build_partition='DSPRHBM',
        hdf5=json.loads((RESULTS / 'hdf5-build/toolchain.json').read_text()),
        torch=json.loads((RESULTS / 'torch-source.json').read_text())))


def check_inventory(name):
    provider = json.loads((RESULTS / 'provider.json').read_text())
    require(provider['purpose'] == 'application-dependencies', 'Unexpected provider')
    package = provider['packages'][name]
    require(package['prefix'] == str(ROOTS[name]), 'Provider prefix changed')
    verify(ROOTS[name], package['files'])


def verify_hdf5():
    check_inventory('qe-hdf5')
    prefix = ROOTS['qe-hdf5']
    require('CMAKE_H5_HAVE_MPI_F08 0' in (prefix/'include/H5config_f.inc').read_text(),
            'Unexpected MPI Fortran interface')
    output = Path('/runtime/hdf5-consumer')
    output.mkdir()
    source = (CONTROL/'cp2k_devtools_smoke.f90').read_text().replace('  use libint_f\n', '')
    (output/'consumer.f90').write_text(source)
    lib = prefix / 'lib'
    compile_output = run([Path(os.environ['MPI_HOME'])/'bin/mpifort',
        '-I'+str(prefix/'include'), '-I'+str(prefix/'mod/static'), output/'consumer.f90',
        lib/'libhdf5_fortran.a', lib/'libhdf5_f90cstub.a', lib/'libhdf5.a',
        '-lsz', '-lz', '-ldl', '-lm', '-o', output/'consumer'])
    env = dict(os.environ, OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
    result = run([Path(env['MPI_HOME'])/'bin/mpiexec', '--allow-run-as-root',
                  '--bind-to', 'none', '-n', '2', output/'consumer'], cwd=output, env=env)
    require('SAI_HDF5_MPI_ROUNDTRIP_PASS' in result, 'HDF5 MPI roundtrip failed')
    (RESULTS/'hdf5-consumer.log').write_text(compile_output+result)
    write(RESULTS/'hdf5-passed.json', dict(passed=True, ranks=2,
          provider_sha256=checksum(RESULTS/'provider.json'), compiler=tool('nvfortran')))


def verify_torch():
    check_inventory('abacus-libtorch')
    prefix = ROOTS['abacus-libtorch']
    output = Path('/runtime/torch-consumer')
    output.mkdir()
    shutil.copyfile(CONTROL/'app_dependency_torch.cpp', output/'consumer.cpp')
    (output/'CMakeLists.txt').write_text('''cmake_minimum_required(VERSION 3.18)
project(torch_consumer LANGUAGES CXX)
find_package(Torch 2.1.2 EXACT CONFIG REQUIRED)
add_executable(consumer consumer.cpp)
target_compile_features(consumer PRIVATE cxx_std_17)
target_link_libraries(consumer PRIVATE "${TORCH_LIBRARIES}")
''')
    log = run(['cmake', '-S', output, '-B', output/'build',
               '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_CXX_COMPILER=g++',
               '-DTorch_DIR='+str(prefix/'share/cmake/Torch')])
    log += run(['cmake', '--build', output/'build', '--parallel', '2'])
    env = dict(os.environ, OMP_NUM_THREADS='1', MKL_NUM_THREADS='1')
    env['LD_LIBRARY_PATH'] = str(prefix/'lib')+':'+env.get('LD_LIBRARY_PATH', '')
    log += run(['ldd', output/'build/consumer'], env=env)
    require('not found' not in log, 'Missing LibTorch runtime library')
    result = run([output/'build/consumer', '/input/deepks.ptg'], cwd=output, env=env)
    require('SAI_LIBTORCH_TENSOR_JIT_PASS 2.1.2' in result and
            'SAI_DEEPKS_MODEL_LOAD_PASS' in result, 'LibTorch consumer failed')
    log += result
    (RESULTS/'torch-consumer.log').write_text(log)
    write(RESULTS/'torch-passed.json', dict(passed=True,
        provider_sha256=checksum(RESULTS/'provider.json'), compiler=tool('g++'),
        tensor_matmul=True, jit_roundtrip=True, abacus_deepks_model_load=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('op', choices=('prefix', 'record-hdf5', 'prepare-torch', 'export', 'verify-hdf5', 'verify-torch'))
    parser.add_argument('name', nargs='?', choices=tuple(ROOTS))
    args = parser.parse_args()
    if args.op == 'prefix':
        require(args.name is not None, 'A library name is required')
        print(ROOTS[args.name])
    else:
        globals()[args.op.replace('-', '_')]()
