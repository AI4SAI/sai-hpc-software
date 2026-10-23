#!/usr/bin/env python3
"""Independent DeePMD ABI, Python/C-API science and measured GPU execution gates."""
import argparse
import ctypes
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from md_evidence import verify_benchmark
from md_probe import libraries
from md_science import (_load_fixture, _record, file_digest, require_execution_context,
                        run_python)

BASELINE = Path('/opt/apps/conda_env/deepmd-kit-3.2.0')
NSYS = '/opt/devtools/nvidia/cuda-12.9.1/nsight-systems-2025.1.3/bin/nsys'
RESOURCES = dict(nodes=1, ranks=1, allocated_gpus=2, inference_gpus=1, threads_per_rank=1)


def inventory(prefix):
    prefix = Path(prefix)
    site = next((prefix / 'lib').glob('python*/site-packages'))
    paths = sorted(set([*(prefix / 'lib').glob('libdeepmd*.so*'),
                        *(site / 'deepmd/lib').glob('libdeepmd*.so*')]))
    backends = sorted({match.group(1) for path in paths
                       if (match := re.match(r'libdeepmd_backend_([^.]+)\.so', path.name))})
    if not {'tf', 'pt', 'jax'} <= set(backends):
        raise ValueError('DeePMD backend feature regression: ' + str(backends))
    if not (prefix / 'lib/libdeepmd_c.so').is_file():
        raise ValueError('standalone DeePMD needs its installed C API')
    return dict(version=importlib.metadata.version('deepmd-kit'), backends=backends,
                libraries={str(path): dict(sha256=file_digest(path), ldd=libraries(path)) for path in paths})


def selected_identity():
    from deepmd_standalone import validate_delivery
    return validate_delivery(json.loads(os.environ['SAI_DEEPMD_DELIVERY']))['identity']


def manifest():
    from export_native import write_manifests
    from release_contract import SOFTWARE
    identity = selected_identity()
    prefix = identity['install_prefix']
    runtime = dict(modules=[name for name in os.environ['LOADEDMODULES'].split(':')
                           if name.split('/')[0] not in {*SOFTWARE, 'apptainer', 'cmake'}],
                   prepend={name: list(dict.fromkeys(str(Path(value).resolve(strict=True))
                                                    for value in os.environ.get(name, '').split(':') if value))
                            for name in ('PATH', 'LD_LIBRARY_PATH', 'PYTHONPATH', 'MODULEPATH')}, set={})
    external = {'/usr', '/lib', '/lib64', str(BASELINE), '/opt/apps/plumed/plumed-2.10.1'}
    for values in runtime['prepend'].values():
        for value in values:
            if value == '/opt/apps/lammps' or value.startswith('/opt/apps/lammps/'):
                raise ValueError('standalone DeepMD runtime depends on an MD engine')
            if not Path(value).is_relative_to(prefix):
                external.add(value)
    write_manifests([dict(identity=identity, commands={'dp': 'bin/dp'},
                          runtime=runtime, external_roots=sorted(external))])


def verify_installation():
    from export_native import MANIFEST_PATH, inventory as native_inventory, read_installed_manifests
    from md_relocate_audit import SITE_ROOTS, check_dynamic, check_metadata, check_reference, check_symlink
    from md_source_version import verify_deepmd_version
    identity = selected_identity()
    prefix = Path(identity['install_prefix'])
    entry = json.loads((prefix / MANIFEST_PATH).read_text())['entry']
    if entry['identity'] != identity or read_installed_manifests([entry]) != native_inventory([entry]):
        raise ValueError('standalone DeePMD SIF content/identity changed')
    observed = inventory(prefix)
    baseline = json.loads(Path('/runtime/baseline-inventory.json').read_text())
    if not set(baseline['backends']) <= set(observed['backends']):
        raise ValueError('standalone DeePMD dropped a baseline backend')
    version = verify_deepmd_version(dict(track=identity['track'], ref=identity['source_ref']), observed['version'])
    roots = [prefix, *map(Path, SITE_ROOTS)]
    inspected = 0
    for path in prefix.rglob('*'):
        if path.is_symlink():
            check_symlink(path, roots)
        elif path.is_file():
            with path.open('rb') as stream:
                magic = stream.read(4)
            if magic == b'\x7fELF':
                dynamic = subprocess.check_output(['readelf', '-d', str(path)], text=True)
                if check_dynamic(dynamic, path, roots):
                    output = libraries(path)
                    for resolved in re.findall(r'(?:=>\s+|^\s*)(/[^\s]+)', output, re.M):
                        check_reference(resolved, roots, resolve=True)
                inspected += 1
            elif path.suffix in ('.cmake', '.pc'):
                check_metadata(path.read_text(errors='replace'), roots)
    if inspected < 4:
        raise ValueError('incomplete DeePMD binary/runtime inventory')
    return dict(verified=True, elf_files=inspected, version=version, inventory=observed)


def run_c_api(case, backend, implementation, prefix):
    require_execution_context()
    fixture = _load_fixture(Path(case), backend)
    lib = ctypes.CDLL(str(Path(prefix) / 'lib/libdeepmd_c.so'), mode=ctypes.RTLD_GLOBAL)
    ptr, dptr, iptr = ctypes.c_void_p, ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_int)
    lib.DP_NewDeepPot.argtypes, lib.DP_NewDeepPot.restype = [ctypes.c_char_p], ptr
    lib.DP_DeepPotCheckOK.argtypes, lib.DP_DeepPotCheckOK.restype = [ptr], ctypes.c_char_p
    lib.DP_DeleteDeepPot.argtypes = [ptr]
    lib.DP_DeepPotCompute.argtypes = [ptr, ctypes.c_int, dptr, iptr, dptr, dptr, dptr, dptr, dptr, dptr]
    model = lib.DP_NewDeepPot(str(Path(case) / fixture['models'][backend]['file']).encode())
    if not model:
        raise ValueError('C API did not construct the requested model')
    records = []
    try:
        error = lib.DP_DeepPotCheckOK(model)
        if error:
            raise ValueError('C API model error: ' + error.decode())
        coordinates = (ctypes.c_double * 18)(*[n for row in fixture['coordinates'] for n in row])
        box = (ctypes.c_double * 9)(*fixture['box'])
        types = (ctypes.c_int * 6)(*fixture['atom_types'])
        for index in range(4):
            energy, force, virial = (ctypes.c_double * 1)(), (ctypes.c_double * 18)(), (ctypes.c_double * 9)()
            begin = time.perf_counter()
            lib.DP_DeepPotCompute(model, 6, coordinates, types, box, energy, force, virial, None, None)
            error = lib.DP_DeepPotCheckOK(model)
            if error:
                raise ValueError('C API compute error: ' + error.decode())
            observed = dict(energy=energy[0], forces=[list(force[i:i + 3]) for i in range(0, 18, 3)],
                            virial=list(virial))
            records.append(_record(fixture, backend, 'c-api', implementation, RESOURCES,
                                   index == 0, time.perf_counter() - begin, observed))
    finally:
        lib.DP_DeleteDeepPot(model)
    return records


def verify_science_files(runtime):
    from md_performance_run import parse_nsight_kernel_trace
    runtime = Path(runtime)
    reports = {}
    for backend in ('tf', 'pt', 'jax'):
        for engine in ('python', 'c-api'):
            records = [row for side in ('baseline', 'candidate')
                       for row in json.loads((runtime / (side + '-' + backend + '-' + engine + '.json')).read_text())]
            if any(row.get('backend') != backend or row.get('engine') != engine for row in records):
                raise ValueError('DeePMD science file contains a different backend or engine')
            reports[backend + '/' + engine] = verify_benchmark(records)
    gpu = parse_nsight_kernel_trace((runtime / 'pt-cuda-kernels.csv').read_text())
    if not (runtime / 'pt-cuda-trace.nsys-rep').is_file():
        raise ValueError('missing independently collected Nsight report')
    gpu.update(report_sha256=file_digest(runtime / 'pt-cuda-trace.nsys-rep'),
               csv_sha256=file_digest(runtime / 'pt-cuda-kernels.csv'), backend='pt', engine='c-api')
    return dict(passed=True, cases=reports, gpu=gpu,
                scope='independent Python/C-API inference; TF/JAX use the existing site CPU frameworks')


def science():
    require_execution_context()
    prefix = selected_identity()['install_prefix']
    runtime = Path('/runtime')
    for side, installation in (('baseline', str(BASELINE)), ('candidate', prefix)):
        env = dict(os.environ)
        # Reset package/library priority for the baseline. Both implementations
        # use the same assigned GPU; no CUDA device is hidden during inference.
        env['PYTHONPATH'] = str(Path(installation) / 'lib/python3.13/site-packages')
        env['LD_LIBRARY_PATH'] = ':'.join([installation + '/lib',
            installation + '/lib/python3.13/site-packages/deepmd/lib',
            *[part for part in os.environ['LD_LIBRARY_PATH'].split(':')
              if not Path(part).is_relative_to(prefix)]])
        for backend in ('tf', 'pt', 'jax'):
            for engine in ('python', 'c-api'):
                name = '-'.join((side, backend, engine))
                command = [installation + '/bin/python', '/control/deepmd_standalone_check.py', 'evaluate',
                           '--backend', backend, '--engine', engine, '--implementation', side,
                           '--prefix', installation, '--output', str(runtime / (name + '.json'))]
                with (runtime / (name + '.log')).open('x') as log:
                    subprocess.run(command, env=env, cwd=runtime, stdout=log, stderr=subprocess.STDOUT, check=True)
    # Trace an additional real C-API inference in the final SIF. Trace timing is
    # kept separate from the unprofiled numeric measurements above.
    profile = [NSYS, 'profile', '--trace=cuda', '--sample=none', '--cpuctxsw=none',
               '--force-overwrite=false', '--output', '/runtime/pt-cuda-trace',
               prefix + '/bin/python', '/control/deepmd_standalone_check.py', 'evaluate',
               '--backend', 'pt', '--engine', 'c-api', '--implementation', 'candidate',
               '--prefix', prefix, '--output', '/runtime/pt-profiled.json']
    with (runtime / 'pt-profiled.log').open('x') as log:
        subprocess.run(profile, cwd=runtime, stdout=log, stderr=subprocess.STDOUT, check=True)
    with (runtime / 'pt-cuda-kernels.csv').open('x') as output, (runtime / 'pt-nsys-stats.log').open('x') as log:
        subprocess.run([NSYS, 'stats', '--report', 'cuda_gpu_trace', '--format', 'csv',
                        '--force-export=true', '/runtime/pt-cuda-trace.nsys-rep'],
                       cwd=runtime, stdout=output, stderr=log, check=True)
    result = verify_science_files(runtime)
    (runtime / 'science.json').write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    inv = sub.add_parser('inventory')
    inv.add_argument('prefix')
    for name in ('manifest', 'verify', 'science'):
        sub.add_parser(name)
    evaluate = sub.add_parser('evaluate')
    evaluate.add_argument('--backend', choices=('tf', 'pt', 'jax'), required=True)
    evaluate.add_argument('--engine', choices=('python', 'c-api'), required=True)
    evaluate.add_argument('--implementation', choices=('baseline', 'candidate'), required=True)
    evaluate.add_argument('--prefix', required=True)
    evaluate.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.command == 'inventory':
        result = inventory(args.prefix)
    elif args.command == 'manifest':
        result = manifest()
    elif args.command == 'verify':
        result = verify_installation()
    elif args.command == 'science':
        result = science()
    else:
        result = (run_python('/runtime/case', args.backend, args.implementation, RESOURCES)
                  if args.engine == 'python' else run_c_api('/runtime/case', args.backend, args.implementation, args.prefix))
        with Path(args.output).open('x') as output:
            json.dump(result, output, sort_keys=True)
            output.write('\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
