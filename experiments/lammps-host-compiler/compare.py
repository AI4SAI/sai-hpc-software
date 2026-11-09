#!/usr/bin/env python3
"""Require matched compiler inputs, allowing only PR 5308 host-only flags."""
from collections import Counter
import importlib.util
import json
from pathlib import Path
import sys

from submit import BASE, HEAD


def compiler_inputs(path):
    helper = Path(__file__).with_name('generator_compare.py')
    if not helper.is_file():
        helper = Path(__file__).parents[1] / 'lammps-ninja/compare.py'
    spec = importlib.util.spec_from_file_location('generator_compare', helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.compiler_inputs(path)


def normalized(inputs, prefix, allow_host_only):
    result, selected = Counter(), Counter()
    for (source, target, args), count in inputs.items():
        flags = args.count('--host-only')
        if flags:
            if (not allow_host_only or flags != 1 or target != 'lammps'
                    or not source.startswith('/workspace/lammps/src/')
                    or source.startswith('/workspace/lammps/src/KOKKOS/')
                    or not source.endswith('.cpp')):
                raise ValueError('unexpected host-only command: ' + source)
            selected[source] += count
        # Separate upstream commits have authentic, different install prefixes.
        args = tuple(arg.replace(prefix, '<LAMMPS_INSTALL_PREFIX>') for arg in args if arg != '--host-only')
        result[(source, target, args)] += count
    return result, selected


def summary(experiment):
    request = json.loads((experiment / 'request.json').read_text())
    variants = {row['label']: row for row in request['variants']}
    if set(variants) != {'base', 'pr5308'} or request['upstream']['base'] != BASE or request['upstream']['head'] != HEAD:
        raise ValueError('unexpected paired source pins')
    inputs, selected, phases = {}, {}, {}
    for label, variant in variants.items():
        run = Path(variant['run'])
        identity = variant['delivery']['identity']
        source = json.loads((run / 'runtime/source.json').read_text())
        expected = BASE if label == 'base' else HEAD
        if source['sha'] != expected or identity['source_sha'] != expected or source['variant'] != label:
            raise ValueError('source receipt does not match the variant')
        inputs[label], selected[label] = normalized(compiler_inputs(run / 'runtime/compile_commands.json'),
                                                   identity['install_prefix'], label == 'pr5308')
        phases[label] = {phase: json.loads((run / 'runtime' / ('cmake-' + phase + '.json')).read_text())
                         for phase in ('configure', 'build', 'install')}
        for row in phases[label].values():
            if row['returncode'] or row['generator'] != 'Unix Makefiles' or str(row['build_jobs']) != '24':
                raise ValueError('failed phase or changed generator/parallelism')
        if len({tuple(row['affinity']) for row in phases[label].values()}) != 1:
            raise ValueError('affinity changed within a variant')
    if inputs['base'] != inputs['pr5308']:
        changes = {label: [dict(source=k[0], target=k[1], arguments=k[2], count=n)
                           for k, n in (inputs[label] - inputs[peer]).items()]
                   for label, peer in (('base', 'pr5308'), ('pr5308', 'base'))}
        (experiment / 'compiler-input-differences.json').write_text(json.dumps(changes, indent=2) + '\n')
        raise ValueError('unexpected compiler input difference; inspect the saved differences')
    if not selected['pr5308']:
        raise ValueError('upstream change did not select any host-only sources')
    for key in ('affinity', 'node', 'job'):
        if phases['base']['build'][key] != phases['pr5308']['build'][key]:
            raise ValueError('paired builds differ in ' + key)
    result = dict(upstream=request['upstream'], build_jobs=24, order=request['order'],
                  compiler_inputs_match_except_host_only_and_install_prefix=True,
                  compiler_commands=sum(inputs['base'].values()),
                  host_only_commands=sum(selected['pr5308'].values()),
                  host_only_sources=sorted(selected['pr5308']), variants={})
    for label, rows in phases.items():
        result['variants'][label] = dict(phases=rows,
            cmake_total_seconds=sum(row['elapsed_seconds'] for row in rows.values()),
            sampled_process_pss_peak_gib=max(row['sampled_sum_pss_peak_kib'] for row in rows.values()) / 1048576)
    base, pr = (result['variants'][label] for label in ('base', 'pr5308'))
    for key, b, p in (('build', base['phases']['build']['elapsed_seconds'], pr['phases']['build']['elapsed_seconds']),
                       ('cmake_total', base['cmake_total_seconds'], pr['cmake_total_seconds'])):
        result[key + '_time_reduction_percent'] = 100 * (b - p) / b
        result[key + '_speedup'] = b / p
    (experiment / 'comparison.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    return result


if __name__ == '__main__':
    print(json.dumps(summary(Path(sys.argv[1])), indent=2, sort_keys=True))
