#!/usr/bin/env python3
"""Select and verify the Volta build for legacy FindCUDA and native CUDA CMake."""
import argparse
import json
from pathlib import Path
import re
import shlex


def native_cuda(source):
    text = Path(source).read_text()
    if re.search(r'^\s*enable_language\s*\(\s*CUDA\s*\)', text, re.M):
        return True
    if re.search(r'^\s*find_package\s*\(\s*CUDA\b', text, re.M):
        return False
    raise ValueError('unrecognized LAMMPS GPU CUDA configuration')


def options(source, host_compiler):
    if native_cuda(source):
        return ['-DGPU_ARCH=70', '-DCMAKE_CUDA_ARCHITECTURES=70',
                '-DCMAKE_CUDA_HOST_COMPILER=' + host_compiler]
    return ['-DGPU_ARCH=sm_70', '-DCUDA_BUILD_MULTIARCH=OFF', '-DCUDPP_OPT=OFF',
            '-DCUDA_HOST_COMPILER=' + host_compiler]


def target_commands(build, target):
    """Inspect generated compiler commands for either Make or Ninja.

    Keep flags.make compatibility for older frozen controllers. An existing
    compile database must be complete; never hide missing Ninja targets by
    falling back to a stale Makefile.
    """
    database = build / 'compile_commands.json'
    if not database.exists():
        return [(build / 'CMakeFiles' / (target + '.dir') / 'flags.make').read_text()]
    commands = []
    pattern = r'(?:^|/)CMakeFiles/' + re.escape(target) + r'\.dir/'
    for entry in json.loads(database.read_text()):
        args = entry.get('arguments') or shlex.split(entry['command'])
        output = entry.get('output')
        if output is None and '-o' in args:
            output = args[args.index('-o') + 1]
        if output and re.search(pattern, output):
            commands.append(shlex.join(args))
    if not commands:
        raise ValueError('missing generated compiler commands for target: ' + target)
    return commands


def verify(source, build):
    build = Path(build)
    modern = native_cuda(source)
    cache = set((build / 'CMakeCache.txt').read_text().splitlines())
    expected = {'FFT_KOKKOS:STRING=CUFFT', 'CUDA_MPS_SUPPORT:BOOL=ON',
                'CMAKE_INSTALL_LIBDIR:PATH=lib',
                'GPU_ARCH:STRING=' + ('70' if modern else 'sm_70')}
    if not modern:
        expected |= {'CUDA_BUILD_MULTIARCH:BOOL=OFF', 'CUDPP_OPT:BOOL=OFF'}
    missing = expected - cache
    if missing:
        raise ValueError('unrecognized or incorrect CMake options: ' + ', '.join(sorted(missing)))
    for target, definition in (('lammps', '-DFFT_KOKKOS_CUFFT'), ('gpu', '-DCUDA_MPS_SUPPORT')):
        if any(definition not in shlex.split(command) for command in target_commands(build, target)):
            raise ValueError('missing generated definition: ' + definition)
    if modern:
        for command in target_commands(build, 'gpu_fatbin'):
            architectures = set(re.findall(r'(?:compute|sm)_([0-9]+)', command))
            if architectures != {'70'}:
                raise ValueError('GPU fatbinary compilation must target only Volta SM70')
    return 'native-cuda' if modern else 'find-cuda'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('options', 'verify'))
    parser.add_argument('source')
    parser.add_argument('argument')
    args = parser.parse_args()
    if args.action == 'options':
        print('\n'.join(options(args.source, args.argument)))
    else:
        print('LAMMPS_CUDA_VERIFIED ' + verify(args.source, args.argument))
