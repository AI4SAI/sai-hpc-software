#!/usr/bin/env python3
"""Summarize a completed pair and reject differences in compilation inputs."""
from collections import Counter
import json
from pathlib import Path
import re
import shlex
import sys


def expand_options(args, directory, seen=()):
    """Read the captured NVCC response files; never assume their contents."""
    result = []
    iterator = iter(args)
    for arg in iterator:
        if arg != '--options-file':
            result.append(arg)
            continue
        name = Path(next(iterator))
        if name.is_absolute() or '..' in name.parts or name in seen or len(seen) >= 16:
            raise ValueError('unrecognized/cyclic NVCC options file: ' + str(name))
        result.extend(expand_options(shlex.split((directory / name).read_text()), directory, (*seen, name)))
    return result


def compiler_inputs(path):
    result = Counter()
    for row in json.loads(path.read_text()):
        args = expand_options(row.get('arguments') or shlex.split(row['command']),
                              path.parent / 'cmake-response-files')
        normalized = []
        output = row.get('output', '')
        i = 0
        while i < len(args):
            # Dependency tracking and output paths do not alter object code.
            if args[i] in ('-o', '-MF', '-MT', '-MQ'):
                if args[i] == '-o':
                    output = args[i + 1]
                i += 2
                continue
            if args[i] in ('-MD', '-MMD', '-MP'):
                i += 1
                continue
            normalized.append(args[i])
            i += 1
        match = re.search(r'(?:^|/)CMakeFiles/([^/]+)\.dir/', output)
        if not match:
            raise ValueError('unknown compiler target: ' + output)
        result[(row['file'], match.group(1), tuple(normalized))] += 1
    return result


def summary(experiment):
    request = json.loads((experiment / 'request.json').read_text())
    variants = {row['label']: Path(row['run']) for row in request['variants']}
    inputs = {name: compiler_inputs(path / 'runtime/compile_commands.json') for name, path in variants.items()}
    if inputs['make'] != inputs['ninja']:
        changes = {name: [dict(source=key[0], target=key[1], arguments=key[2], count=count)
                          for key, count in (inputs[name] - inputs[peer]).items()]
                   for name, peer in (('make', 'ninja'), ('ninja', 'make'))}
        (experiment / 'compiler-input-differences.json').write_text(json.dumps(changes, indent=2) + '\n')
        raise ValueError('compiler inputs differ; inspect compiler-input-differences.json before comparing time')
    result = dict(source_sha=request['delivery']['identity']['source_sha'], build_jobs=request['build_jobs'],
                  order=request['order'], compiler_inputs_identical=True,
                  compiler_commands=sum(inputs['make'].values()),
                  targets=dict(Counter({target: sum(n for (_, t, _), n in inputs['make'].items() if t == target)
                                        for _, target, _ in inputs['make']})), variants={})
    for name, path in variants.items():
        phases = {phase: json.loads((path / 'runtime' / ('cmake-' + phase + '.json')).read_text())
                  for phase in ('configure', 'build', 'install')}
        if any(row['returncode'] for row in phases.values()):
            raise ValueError('one measured phase failed')
        if len({tuple(row['affinity']) for row in phases.values()}) != 1:
            raise ValueError('CPU affinity changed within a variant')
        result['variants'][name] = dict(phases=phases,
            cmake_total_seconds=sum(row['elapsed_seconds'] for row in phases.values()),
            sampled_process_pss_peak_gib=max(row['sampled_sum_pss_peak_kib'] for row in phases.values()) / 1048576)
    make, ninja = (result['variants'][name] for name in ('make', 'ninja'))
    if make['phases']['build']['affinity'] != ninja['phases']['build']['affinity']:
        raise ValueError('paired builds used different CPU affinities')
    if make['phases']['build']['node'] != ninja['phases']['build']['node']:
        raise ValueError('paired builds ran on different nodes')
    for key, m, n in (('build', make['phases']['build']['elapsed_seconds'], ninja['phases']['build']['elapsed_seconds']),
                      ('cmake_total', make['cmake_total_seconds'], ninja['cmake_total_seconds'])):
        result[key + '_time_reduction_percent'] = 100 * (m - n) / m
        result[key + '_speedup'] = m / n
    (experiment / 'comparison.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    return result


if __name__ == '__main__':
    print(json.dumps(summary(Path(sys.argv[1])), indent=2, sort_keys=True))
