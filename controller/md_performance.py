#!/usr/bin/env python3
"""Representative engine-timing contract, separate from six-atom science smoke.

The workload is explicitly a synthetic fixed-geometry force-evaluation benchmark
(6 * 7**3 = 2058 atoms), not an equilibrated water simulation. Calibration uses
baseline LAMMPS engine time, freezes steps/input, then BOTH implementations need
warmup + 3 repeats. A fast candidate below the minimum interval requires a new
common calibration, never a separately changed candidate workload.
"""
import hashlib
import json
import math
import re
import statistics
from md_science import render_lammps_input

ATOMS = 2058
MIN_ENGINE_SECONDS = 3.0


def render_input(backend, steps):
    if type(steps) is not int or not 1 <= steps <= 10_000_000:
        raise ValueError('performance step count outside policy')
    text = render_lammps_input(backend)
    text = text.replace('read_data data.lmp\n', 'read_data data.lmp\nreplicate 7 7 7\n')
    # The warmup is INSIDE the process and excluded from the measured Loop time.
    # Whole-process walltime remains separately reported by the runner.
    return text.replace('run 1\n', 'run 1 post no\n' + f'run {steps} pre no\n')


def parse_engine_time(stdout, steps):
    matches = re.findall(r'^Loop time of ([0-9.eE+-]+) on ([0-9]+) procs for ([0-9]+) steps with ([0-9]+) atoms\s*$', stdout, re.M)
    if len(matches) != 1:
        raise ValueError('exactly one measured LAMMPS engine interval is required')
    seconds, procs, actual_steps, atoms = matches[0]
    seconds = float(seconds)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError('invalid LAMMPS engine timing')
    if int(actual_steps) != steps or int(atoms) != ATOMS or int(procs) < 1:
        raise ValueError('LAMMPS measured a different workload or rank count')
    return {'engine_seconds': seconds, 'ranks': int(procs), 'steps': steps,
            'atoms': ATOMS, 'atom_steps_per_second': ATOMS * steps / seconds}


def calibrate(measure, backend, fixture_sha256, node, resources, *, minimum=5.0, initial_steps=100, max_attempts=6):
    """measure(steps,input_text) must run the BASELINE on an allocated node.

    Its result is raw LAMMPS stdout, not a caller-supplied timing estimate. The
    scientific execution guard lives in that runner; this pure driver is also
    used by unit tests without invoking simulation software.
    """
    if not re.fullmatch(r'[a-f0-9]{64}', fixture_sha256) or not node or not resources:
        raise ValueError('calibration lacks immutable fixture/resource identity')
    if not math.isfinite(minimum) or minimum < MIN_ENGINE_SECONDS:
        raise ValueError('calibration interval too short')
    steps = initial_steps
    history = []
    for _ in range(max_attempts):
        text = render_input(backend, steps)
        timing = parse_engine_time(measure(steps, text), steps)
        history.append(timing)
        if timing['engine_seconds'] >= minimum:
            input_sha = hashlib.sha256((fixture_sha256 + '\n' + text).encode()).hexdigest()
            return {'schema': 1, 'workload': 'synthetic replicated fixed-geometry force evaluation',
                    'backend': backend, 'atoms': ATOMS, 'steps': steps, 'node': node,
                    'resources': resources, 'fixture_sha256': fixture_sha256,
                    'input_sha256': input_sha, 'input': text, 'calibration': history,
                    'minimum_engine_seconds': MIN_ENGINE_SECONDS}
        steps = max(steps + 1, math.ceil(steps * minimum / timing['engine_seconds'] * 1.2))
        if steps > 10_000_000:
            raise ValueError('calibrated workload exceeds step limit')
    raise ValueError('baseline failed to reach a representative engine interval')


def verify_performance(records, frozen):
    """Validate identical measured workload and device evidence, report throughput.

    Records must contain raw stdout, wall_seconds, implementation, warmup,
    input_sha256, node/resources, and execution_device={kind,backend,evidence}.
    The evidence is retained for review; this function does not turn an env flag
    or mere GPU availability into proof of actual GPU execution. The caller must
    set device_verified only after validating an actual backend execution trace.
    """
    identity = hashlib.sha256((frozen['fixture_sha256'] + '\n' + frozen['input']).encode()).hexdigest()
    if (identity != frozen['input_sha256'] or frozen['input'] != render_input(frozen['backend'], frozen['steps'])
            or frozen['atoms'] != ATOMS):
        raise ValueError('frozen performance workload changed')
    groups = {'baseline': [], 'candidate': []}
    device_identity = None
    for record in records:
        name = record.get('implementation')
        if name not in groups or type(record.get('warmup')) is not bool:
            raise ValueError('invalid performance implementation/warmup')
        if record.get('profiled', False) is not False:
            raise ValueError('profiled intervals cannot be used for the speed comparison')
        for field in ('node', 'resources', 'input_sha256'):
            if record.get(field) != frozen[field]:
                raise ValueError('performance hardware, allocation or input differs')
        device = record.get('execution_device', {})
        if (device.get('kind') not in ('cpu', 'gpu') or device.get('backend') != frozen['backend']
                or device.get('device_verified') is not True or not device.get('evidence')):
            raise ValueError('actual backend execution device has not been proven')
        # Allocated probe 1271205 confirmed BOTH TF and JAX CPU-only. A GPU
        # replacement is a different capability, not a same-device speed test.
        if frozen['backend'] in ('tf', 'jax') and device['kind'] != 'cpu':
            raise ValueError('site TensorFlow/JAX baseline is CPU-only')
        current = (device['backend'], device['kind'], device.get('model'))
        if device_identity is None:
            device_identity = current
        elif current != device_identity:
            raise ValueError('CPU/GPU or GPU model mismatch is not a fair speed comparison')
        timing = parse_engine_time(record['stdout'], frozen['steps'])
        wall = record.get('wall_seconds')
        if (isinstance(wall, bool) or not isinstance(wall, (int, float)) or not math.isfinite(wall)
                or wall < timing['engine_seconds']):
            raise ValueError('invalid end-to-end walltime')
        if timing['engine_seconds'] < MIN_ENGINE_SECONDS:
            raise ValueError('engine interval too short; recalibrate BOTH implementations')
        if record.get('scientific_verified') is not True:
            raise ValueError('large-system numerical correctness is unverified')
        groups[name].append(dict(timing, warmup=record['warmup'], wall_seconds=wall))
    result = {}
    for name, rows in groups.items():
        measured = [row for row in rows if not row['warmup']]
        if not any(row['warmup'] for row in rows) or len(measured) < 3:
            raise ValueError('each implementation needs warmup and at least three timed repeats')
        result[name] = {'median_engine_seconds': statistics.median(row['engine_seconds'] for row in measured),
                        'median_wall_seconds': statistics.median(row['wall_seconds'] for row in measured),
                        'median_atom_steps_per_second': statistics.median(row['atom_steps_per_second'] for row in measured)}
    result['speedup'] = result['baseline']['median_engine_seconds'] / result['candidate']['median_engine_seconds']
    result['device'] = device_identity
    result['scope'] = frozen['workload']
    return result
