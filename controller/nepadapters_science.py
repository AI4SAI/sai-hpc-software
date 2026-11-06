#!/usr/bin/env python3
"""Check the real LAMMPS plugin against pinned energy/force/virial labels."""
import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess

from source_cache import checksum

TOLERANCES = dict(energy_diff=1e-6, eatom_sum_diff=1e-6,
                  max_force_component_diff=1e-6, max_virial_component_diff=5e-6)
BOUNDARIES = ('p p p', 'p p f', 'f f f')
CASE_SHA256 = {
    'nep.txt': 'c772e70b32e5a085936f04e5f5382c7af88a03cb68765510ac82f71ae97eab2c',
    'train.xyz': '9679ecdac1ea13300f9bf208b7d4393e5dfd0e0527a46653de38e4c80bff74ab',
    'baseline.py': 'b60bf77c936f6bc0f862c6e588c26c7af6b80d328cfb7a0f646c61cf9bed4835',
}


def prepare(source, destination):
    destination.mkdir(parents=True)
    for name in ('nep.txt', 'train.xyz'):
        shutil.copyfile(source / 'tests/fixtures/cpu_baseline' / name, destination / name)
    shutil.copyfile(source / 'tools/run_lammps_baseline_smoke.py', destination / 'baseline.py')


def helper(cases):
    for name, sha in CASE_SHA256.items():
        if checksum(cases / name) != sha:
            raise ValueError('pinned NEP baseline changed: ' + name)
    spec = importlib.util.spec_from_file_location('nepa_baseline', cases / 'baseline.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(result):
    for key, limit in TOLERANCES.items():
        if (key not in result or isinstance(result[key], bool) or
                not isinstance(result[key], (int, float)) or not math.isfinite(result[key]) or
                not 0 <= result[key] <= limit):
            raise ValueError(f'NEP numerical acceptance failed: {key}={result.get(key)} > {limit}')


def compare_raw(h, fixture, work):
    rows, thermo = h.parse_dump(work / 'dump.out'), h.parse_thermo(work / 'log.lammps')
    if set(rows) != {a['id'] for a in fixture['atoms']} or thermo.get('Atoms') != len(rows):
        raise ValueError('missing/extra atoms in NEP output')
    # Upstream max(0, abs(NaN)) can mask an invalid force component.
    if any(not math.isfinite(v) for row in [*rows.values(), thermo] for v in row.values()):
        raise ValueError('non-finite NEP output')
    result = h.compare(fixture, rows, thermo)
    check(result)
    return result


def check_gpu_banner(screen):
    if (not re.search(r'KOKKOS mode(?: with Kokkos version [0-9.]+)? is enabled', screen) or
            'using double precision' not in screen or 'backend: cuda' not in screen or
            'kokkos_device' not in screen):
        raise ValueError('GPU test did not use double precision CUDA Kokkos')


def verify(cases, output):
    """Replay checks from raw outputs; summary booleans are not release evidence."""
    h = helper(cases)
    fixture = h.read_fixture(cases / 'train.xyz')
    record = json.loads((output / 'acceptance.json').read_text())
    if record.get('passed') is not True or set(record['static']) != {'cpu', 'gpu'}:
        raise ValueError('incomplete NEP acceptance')
    boundary = record.get('boundary')
    if boundary not in BOUNDARIES:
        raise ValueError('missing or unsupported boundary acceptance')
    for backend in ('cpu', 'gpu'):
        work = output / backend
        screen = (work / 'screen.out').read_text()
        if ('boundary ' + boundary) not in (work / 'in.baseline').read_text().splitlines():
            raise ValueError('boundary input differs from acceptance')
        h.validate_startup_banner(screen, 'nep/' + backend)
        if backend == 'gpu':
            check_gpu_banner(screen)
        if compare_raw(h, fixture, work) != record['static'][backend]:
            raise ValueError('NEP summary differs from raw numerical output')
    thermo = h.parse_thermo(output / 'gpu/dynamics.log')
    if (thermo.get('Step') != 100 or any(not math.isfinite(v) for v in thermo.values()) or
            thermo != record['gpu_dynamics']):
        raise ValueError('incomplete/non-finite NEP GPU dynamics')
    if boundary == BOUNDARIES[0]:
        if set(record.get('boundary_cases', {})) != set(BOUNDARIES[1:]):
            raise ValueError('mixed/nonperiodic boundary acceptance is incomplete')
        for mode in BOUNDARIES[1:]:
            if verify(cases, output / ('boundary-' + mode.replace(' ', ''))) != record['boundary_cases'][mode]:
                raise ValueError('boundary receipt differs from raw outputs')
    return record


def run(cases, lmp, plugins, output, launcher=(), gpus=1, boundary='p p p'):
    h = helper(cases)
    fixture = h.read_fixture(cases / 'train.xyz')
    # A translation preserves the labels and places interacting atoms across
    # the domain midpoint, so every rank participates in two-rank tests.
    for atom in fixture['atoms']:
        atom['position'] = [x + 5.0 for x in atom['position']]
        if boundary == 'p p f':
            # Move the cluster across the periodic x face. Its nonperiodic z
            # faces remain farther away than the model cutoff, preserving the
            # pinned reference while exercising periodic ghosts with mixed PBC.
            atom['position'][0] = (atom['position'][0] + 8.0) % fixture['lattice'][0]
    elements = h.read_type_map(cases / 'nep.txt')
    output.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, LAMMPS_PLUGIN_PATH=str(plugins), OMP_NUM_THREADS='1')
    records = {}
    for backend in ('cpu', 'gpu'):
        work = output / backend
        work.mkdir()
        h.write_data(work / 'data.baseline', fixture, elements)
        h.write_input(work / 'in.baseline', '', cases / 'nep.txt', elements,
                      'nep/' + backend, 'environment', 'off' if backend == 'gpu' else 'on')
        input_path = work / 'in.baseline'
        input_path.write_text(input_path.read_text().replace('boundary p p p', 'boundary ' + boundary))
        argv = [*launcher, str(lmp)]
        if backend == 'gpu':
            argv += ['-k', 'on', 'g', str(gpus), '-pk', 'kokkos', 'neigh', 'full',
                     'comm', 'device', 'gpu/aware', 'on']
        argv += ['-in', 'in.baseline', '-log', 'log.lammps']
        with (work / 'screen.out').open('w') as stream:
            subprocess.run(argv, cwd=work, env=env, stdout=stream, stderr=subprocess.STDOUT, check=True)
        screen = (work / 'screen.out').read_text()
        h.validate_startup_banner(screen, 'nep/' + backend)
        if backend == 'gpu':
            check_gpu_banner(screen)
        result = compare_raw(h, fixture, work)
        records[backend] = result
    # Exercise repeated force evaluation and integration, not only run 0.
    work = output / 'gpu'
    (work / 'dump.out').rename(work / 'static.dump')
    text = (work / 'in.baseline').read_text().replace('run 0',
        'velocity all create 30.0 112358 mom yes rot no\n'
        'timestep 0.00001\nfix integrate all nve/kk\nrun 100')
    (work / 'in.dynamics').write_text(text)
    argv[-3:] = ['in.dynamics', '-log', 'dynamics.log']
    with (work / 'dynamics.out').open('w') as stream:
        subprocess.run(argv, cwd=work, env=env, stdout=stream, stderr=subprocess.STDOUT, check=True)
    thermo = h.parse_thermo(work / 'dynamics.log')
    if thermo['Step'] != 100 or any(not math.isfinite(x) for x in thermo.values()):
        raise ValueError('GPU dynamics did not reach 100 finite steps')
    (work / 'dump.out').rename(work / 'dynamics.dump')
    (work / 'static.dump').rename(work / 'dump.out')
    boundary_cases = {}
    if boundary == BOUNDARIES[0]:
        for mode in BOUNDARIES[1:]:
            boundary_cases[mode] = run(cases, lmp, plugins, output / ('boundary-' + mode.replace(' ', '')),
                                       launcher, gpus, mode)
    record = dict(schema=2, passed=True, boundary=boundary, boundary_cases=boundary_cases,
                  tolerances=TOLERANCES, static=records,
                  gpu_dynamics=thermo, lmp=str(lmp), plugins=str(plugins),
                  launcher=list(launcher), gpus_per_node=gpus,
                  plugin_sha256={p.name: checksum(p) for p in sorted(plugins.glob('*plugin.so'))})
    (output / 'acceptance.json').write_text(json.dumps(record, indent=2) + '\n')
    verify(cases, output)
    print(json.dumps(record), flush=True)
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare')
    p.add_argument('source', type=Path)
    p.add_argument('destination', type=Path)
    p = commands.add_parser('run')
    for name in ('cases', 'lmp', 'plugins', 'output'):
        p.add_argument(name, type=Path)
    p.add_argument('--launcher', default='[]', help='JSON argv for host MPI')
    p.add_argument('--gpus', type=int, default=1)
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.source, args.destination)
    else:
        run(args.cases, args.lmp, args.plugins, args.output, json.loads(args.launcher), args.gpus)
