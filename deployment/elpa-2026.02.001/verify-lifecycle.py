#!/usr/bin/env python3
"""Reject missing MPI instrumentation, leaked communicators or failed solves."""
import argparse
import json
import math
from pathlib import Path
import re


def validate(text, ranks, gpu, objects):
    expected = objects if gpu else 0
    traces = re.findall(r'^ELPA_COMM_TRACE created=(\d+) explicitly_freed=(\d+) live_at_exit=(\d+)$', text, re.M)
    if len(traces) != ranks or any(tuple(map(int, row)) != (expected, expected, 0) for row in traces):
        raise ValueError(f'Communicator lifetime failed: expected {ranks} reports of {(expected, expected, 0)}, got {traces}')
    lines = [line for line in text.splitlines() if line.startswith('ELPA_NUMERIC ')]
    if len(lines) != 2 * objects:
        raise ValueError('Missing repeated numerical solves')
    maxima = dict.fromkeys(('spectrum', 'residual', 'orthogonality'), 0.0)
    for index, line in enumerate(lines):
        fields = dict(re.findall(r'(\w+)=(\S+)', line))
        identity = dict(ranks=str(ranks), gpu=str(gpu), solver='2', complex='0', nonblocking='0', repeat=str(index % 2))
        if not line.startswith('ELPA_NUMERIC PASS ') or any(fields.get(k) != v for k, v in identity.items()):
            raise ValueError('Unexpected numerical solver, rank count or repeat')
        for key in maxima:
            value = float(fields[key])
            if not math.isfinite(value) or not 0 <= value < 1e-10:
                raise ValueError(f'Invalid numerical error: {key}={value}')
            maxima[key] = max(maxima[key], value)
    return dict(ranks=ranks, gpu=gpu, objects_per_rank=objects, numerical_solves=len(lines),
                created_per_rank=expected, freed_per_rank=expected, live_per_rank=0, max_errors=maxima)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--ranks', type=int, required=True, choices=(1, 2))
    parser.add_argument('--gpu', type=int, required=True, choices=(0, 1))
    parser.add_argument('--objects', type=int, required=True, choices=range(1, 129))
    args = parser.parse_args()
    print(json.dumps(validate(args.log.read_text(), args.ranks, args.gpu, args.objects), sort_keys=True))
