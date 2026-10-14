"""Rebuild MD performance acceptance from raw, image-bound trial evidence.

The caller authenticates ``case_dir`` against the locked upstream scientific
oracle and supplies allocation/image identity from its immutable request. This
module never trusts a cached science/device/performance ``passed`` flag. Nsight
statistics are regenerated from each raw report; simulation software is never
executed during verification.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess

from md_performance import MIN_ENGINE_SECONDS, parse_engine_time, render_input, verify_performance
from md_performance_run import (_fixture_digest, parse_nsight_kernel_trace,
                                verify_large_science)
from md_science import BACKENDS, _load_fixture, file_digest

_SHA256 = re.compile(r'[0-9a-f]{64}\Z')
_BINDING_FIELDS = ('logical_cpus', 'physical_cores', 'omp_threads',
                   'dp_intra_threads', 'dp_inter_threads', 'node', 'job')
_IDENTITY_FIELDS = ('artifact', 'artifact_sha256', 'target', 'implementation')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON evidence field: ' + key)
        result[key] = value
    return result


def _json(path):
    def reject_constant(value):
        raise ValueError('nonfinite JSON evidence: ' + value)
    try:
        return json.loads(path.read_text(), object_pairs_hook=_unique_object,
                          parse_constant=reject_constant)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError('missing or invalid JSON evidence: ' + str(path)) from error


def _safe(path, *, directory=False):
    path = Path(path).absolute()
    if (path.resolve() != path or path.is_symlink()
            or not (path.is_dir() if directory else path.is_file())):
        raise ValueError('missing or untrusted evidence path: ' + str(path))
    return path


class _Verifier:
    def __init__(self, output, case, backend, expected):
        self.output = _safe(output, directory=True)
        self.case = _safe(case, directory=True)
        self.backend, self.expected, self.files = backend, expected, {}
        if backend not in BACKENDS:
            raise ValueError('unknown performance backend')
        if (not isinstance(expected, dict) or not str(expected.get('slurm_job', '')).isdigit()
                or not isinstance(expected.get('resources'), dict)
                or type(expected['resources'].get('ranks')) is not int
                or expected['resources']['ranks'] != 1
                or type(expected['resources'].get('threads_per_rank', 1)) is not int
                or expected['resources'].get('threads_per_rank', 1) != 1
                or any(not _SHA256.fullmatch(str(expected.get(key, '')))
                       for key in ('launcher_sha256', 'artifact_sha256'))):
            raise ValueError('incomplete immutable performance context')
        for key in ('artifact', 'candidate_executable', 'baseline_executable'):
            if not isinstance(expected.get(key), str) or not Path(expected[key]).is_absolute():
                raise ValueError('absolute image and executable identities are required')
        if not expected.get('target'):
            raise ValueError('missing performance target')
        image = _safe(expected['artifact'])
        if file_digest(image) != expected['artifact_sha256']:
            raise ValueError('candidate image checksum changed')
        fixture_path = _safe(self.case / 'fixture.json')
        fixture = _json(fixture_path)
        if fixture.get('models', {}).get(backend, {}).get('file') != BACKENDS[backend]:
            raise ValueError('unexpected fixture model path')
        self.fixture = _load_fixture(self.case, backend)
        for name in (BACKENDS[backend], 'data.lmp', 'plumed.dat', f'in.{backend}'):
            self._input_digest(self.case / name)
        self.fixture_sha = _fixture_digest(self.case, backend)
        self.binding = None
        baseline = Path(expected['baseline_executable']).resolve(strict=True)
        self.baseline_sha256 = file_digest(_safe(baseline))
        self.binaries = {}
        self.frozen = self.read('frozen.json')
        if (self.frozen.get('backend') != backend
                or self.frozen.get('resources') != expected['resources']
                or not self.frozen.get('node')
                or self.frozen.get('fixture_sha256') != self.fixture_sha
                or self.frozen.get('minimum_engine_seconds') != MIN_ENGINE_SECONDS):
            raise ValueError('frozen performance allocation or fixture differs')

    def path(self, relative):
        path = _safe(self.output / relative)
        if not path.is_relative_to(self.output):
            raise ValueError('evidence escaped the performance directory')
        self.files[path.relative_to(self.output).as_posix()] = file_digest(path)
        return path

    def read(self, relative):
        return _json(self.path(relative))

    def text(self, relative):
        return self.path(relative).read_text()

    def _input_digest(self, path):
        path = _safe(path, directory=path.is_dir())
        if path.is_dir():
            children = list(path.rglob('*'))
            if not any(child.is_file() for child in children):
                raise ValueError('empty model directory')
            for child in children:
                if child.is_symlink() or child.resolve() != child:
                    raise ValueError('untrusted model input')
                if child.is_file() and child.is_relative_to(self.output):
                    self.path(child.relative_to(self.output))
        elif path.is_relative_to(self.output):
            self.path(path.relative_to(self.output))
        return file_digest(path)

    def trial(self, record, name, input_text, steps, *, profiled=False):
        if not isinstance(record, dict) or record.get('trial') != name:
            raise ValueError('mislabelled performance trial')
        if record != self.read(name + '/record.json'):
            raise ValueError('aggregate record differs from raw trial record')
        side = record.get('implementation')
        if side not in ('baseline', 'candidate') or record.get('profiled') is not profiled:
            raise ValueError('wrong implementation or profiling mode')
        trial = self.output / name
        for filename in (BACKENDS[self.backend], 'data.lmp', 'plumed.dat', f'in.{self.backend}'):
            if self._input_digest(trial / filename) != self._input_digest(self.case / filename):
                raise ValueError('measured model or scientific input changed')
        if self.text(name + '/in.performance') != input_text:
            raise ValueError('raw measured performance input differs')
        input_sha = hashlib.sha256((self.fixture_sha + '\n' + input_text).encode()).hexdigest()
        if record.get('input_sha256') != input_sha:
            raise ValueError('measured performance input checksum differs')
        stdout = self.text(name + '/stdout.txt')
        if record.get('stdout') != stdout:
            raise ValueError('JSON timing output differs from raw stdout')
        timing = parse_engine_time(stdout, steps)
        if timing['ranks'] != 1:
            raise ValueError('raw engine rank count differs from allocation')
        science = verify_large_science(stdout, self.text(name + '/result.dump'),
                                       self.text(name + '/COLVAR'), self.fixture)
        process = self.read(name + '/process.json')
        if (type(process.get('returncode')) is not int or process['returncode'] != 0
                or process.get('wall_seconds') != record.get('wall_seconds')):
            raise ValueError('raw process did not succeed or walltime differs')
        execution = self.read(name + '/execution.json')
        binding = self.read(name + '/performance-binding.json')
        for key, value in [('node', self.frozen['node']), ('resources', self.expected['resources']),
                           ('slurm_job', self.expected['slurm_job']), ('implementation', side)]:
            if record.get(key) != value or execution.get(key) != value:
                raise ValueError('trial allocation or implementation identity differs')
        for key in _IDENTITY_FIELDS:
            value = side if key == 'implementation' else self.expected[key]
            if execution.get(key) != value or binding.get(key) != value:
                raise ValueError('final-container image/target identity differs')
        if (binding.get('node') != self.frozen['node']
                or binding.get('job') != self.expected['slurm_job']
                or any(binding.get(key) != '1' for key in _BINDING_FIELDS[2:5])):
            raise ValueError('final-container thread/node/job binding differs')
        cpus, cores = binding.get('logical_cpus'), binding.get('physical_cores')
        if (not isinstance(cpus, list) or len(cpus) != 1 or type(cpus[0]) is not int or cpus[0] < 0
                or not isinstance(cores, list) or len(cores) != 1 or not isinstance(cores[0], list)
                or len(cores[0]) != 2 or any(type(value) is not int or value < 0 for value in cores[0])):
            raise ValueError('one measured physical core is required')
        fixed = {key: binding[key] for key in _BINDING_FIELDS}
        if record.get('binding') != fixed or self.binding is not None and self.binding != fixed:
            raise ValueError('baseline/candidate or repeat CPU binding changed')
        self.binding = fixed
        command = execution.get('command')
        if (not isinstance(command, list) or len(command) != 10
                or command[:4] != ['taskset', '--cpu-list', str(cpus[0]), 'bash']
                or command[5:] != ['lmp', '-log', 'log.lammps', '-in', 'in.performance']
                or execution.get('launcher_sha256') != self.expected['launcher_sha256']):
            raise ValueError('benchmark launcher command changed')
        if file_digest(_safe(command[4])) != self.expected['launcher_sha256']:
            raise ValueError('actual benchmark launcher checksum changed')
        child = [self.expected[side + '_executable'], '-log', 'log.lammps', '-in', 'in.performance']
        if profiled:
            child = [self.expected['nsys'], 'profile', '--trace=cuda', '--sample=none',
                     '--cpuctxsw=none', '--force-overwrite=false', '--output', '/work/cuda-trace', *child]
        if binding.get('argv') != child:
            raise ValueError('final-container executable or profiler command changed')
        binary_sha = binding.get('executable_sha256')
        if (binding.get('executable') != self.expected[side + '_executable']
                or not isinstance(binary_sha, str) or not _SHA256.fullmatch(binary_sha)):
            raise ValueError('measured executable checksum is missing or mislabelled')
        if side == 'baseline' and binary_sha != self.baseline_sha256:
            raise ValueError('measured baseline executable differs from current site binary')
        if side in self.binaries and self.binaries[side] != binary_sha:
            raise ValueError('executable bytes changed between calibration, trace or trials')
        self.binaries[side] = binary_sha
        # Cached science flags may be stale; only this raw reparse sets success.
        return dict(record, stdout=stdout, scientific_verified=science['passed']), execution

    def cpu_device(self, record, execution):
        device = self.read(record['trial'] + '/device.json')
        if execution.get('CUDA_VISIBLE_DEVICES') != '':
            raise ValueError('CPU-only backend did not hide CUDA devices')
        evidence = device.get('evidence', {})
        if (device != record.get('execution_device') or device.get('kind') != 'cpu'
                or device.get('backend') != self.backend
                or device.get('model') != 'CPU on ' + self.frozen['node']
                or evidence.get('command') != execution['command']
                or evidence.get('stdout_sha256') != self.files[record['trial'] + '/stdout.txt']
                or evidence.get('returncode') != 0):
            raise ValueError('CPU device evidence differs from raw execution')
        return dict(device, device_verified=True)

    def trace(self, side):
        nsys = self.expected.get('nsys')
        if (not isinstance(nsys, str) or not nsys.startswith('/opt/devtools/')
                or '..' in Path(nsys).parts):
            raise ValueError('trusted site Nsight executable is required')
        name = 'trace-' + side
        record = self.read(name + '/record.json')
        if record.get('implementation') != side:
            raise ValueError('trace belongs to a different implementation')
        checked, execution = self.trial(record, name, self.frozen['input'], self.frozen['steps'], profiled=True)
        device = self.read(name + '/device.json')
        evidence = device.get('evidence', {})
        report = self.path(name + '/cuda-trace.nsys-rep')
        raw_csv = self.text(name + '/cuda-kernels.csv')
        stats_command = [nsys, 'stats', '--report', 'cuda_gpu_trace', '--format', 'csv', '--timeunit', 'ns', str(report)]
        if (device != checked.get('execution_device') or device.get('kind') != 'gpu'
                or device.get('backend') != 'pt' or evidence.get('trial') != name
                or evidence.get('profile_command') != execution['command']
                or evidence.get('stats_command') != stats_command
                or evidence.get('report_sha256') != self.files[name + '/cuda-trace.nsys-rep']
                or evidence.get('csv_sha256') != self.files[name + '/cuda-kernels.csv']):
            raise ValueError('Nsight trace provenance or checksum differs')
        parsed = parse_nsight_kernel_trace(raw_csv)
        regenerated = subprocess.run(stats_command, text=True, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, timeout=180, check=True).stdout
        # Compare normalized CSV as well as semantics: the same totals alone
        # could conceal substituted names/timestamps or memory-transfer rows.
        def rows(value):
            import csv
            import io
            rows, started = [], False
            for row in csv.reader(io.StringIO(value)):
                if {'Start (ns)', 'Duration (ns)', 'Name', 'Device'} <= set(row):
                    started = True
                if started and any(item.strip() for item in row):
                    rows.append(row)
            return rows
        if rows(regenerated) != rows(raw_csv) or parse_nsight_kernel_trace(regenerated) != parsed:
            raise ValueError('stored CUDA CSV does not derive from the raw Nsight report')
        if evidence.get('trace') != parsed or device.get('model') != parsed['devices'][0]:
            raise ValueError('GPU execution summary differs from raw kernels')
        return dict(device, device_verified=True)

    def calibration(self):
        history = self.frozen.get('calibration')
        records = self.read('calibration-records.json')
        if (not isinstance(history, list) or not 1 <= len(history) <= 6
                or not isinstance(records, list) or len(records) != len(history)):
            raise ValueError('raw baseline calibration history is missing')
        for index, (record, timing) in enumerate(zip(records, history)):
            if (not isinstance(timing, dict) or record.get('implementation') != 'baseline'
                    or record.get('warmup') is not False):
                raise ValueError('calibration must measure the baseline')
            steps = timing.get('steps')
            value, execution = self.trial(record, f'calibration-{index:02d}',
                                           render_input(self.backend, steps), steps)
            if parse_engine_time(value['stdout'], steps) != timing:
                raise ValueError('calibration summary differs from raw timing')
            if self.backend in ('tf', 'jax'):
                self.cpu_device(value, execution)
        if history[-1]['steps'] != self.frozen['steps'] or history[-1]['engine_seconds'] < 5.0:
            raise ValueError('baseline calibration did not freeze a representative interval')

    def run(self):
        self.calibration()
        frozen, records = self.frozen, self.read('records.json')
        if not isinstance(records, list) or len(records) < 8 or len(records) % 2:
            raise ValueError('need alternating warmup and three measured pairs')
        traces = {side: self.trace(side) for side in ('baseline', 'candidate')} if self.backend == 'pt' else {}
        checked = []
        for position, record in enumerate(records):
            index, side = position // 2, ('baseline', 'candidate')[position % 2]
            name = f'{side}-{index:02d}'
            if record.get('implementation') != side or record.get('warmup') is not (index == 0):
                raise ValueError('performance trials must alternate after a paired warmup')
            value, execution = self.trial(record, name, frozen['input'], frozen['steps'])
            if self.backend == 'pt':
                device = self.read(name + '/device.json')
                proof = traces[side]
                evidence = device.get('evidence', {})
                if (device != record.get('execution_device')
                        or any(device.get(key) != proof[key] for key in ('kind', 'backend', 'model'))
                        or any(evidence.get(key) != item for key, item in proof['evidence'].items())
                        or evidence.get('measured_stdout_sha256') != self.files[name + '/stdout.txt']):
                    raise ValueError('measured trial refers to a different GPU trace')
                value['execution_device'] = dict(device, device_verified=True)
            else:
                value['execution_device'] = self.cpu_device(value, execution)
            checked.append(value)
        performance = verify_performance(checked, frozen)
        performance['device'] = list(performance['device'])
        if file_digest(_safe(Path(self.expected['baseline_executable']).resolve(strict=True))) != self.baseline_sha256:
            raise ValueError('site baseline executable changed during verification')
        # report.json is deliberately not an input to the acceptance decision.
        return {'passed': True, 'performance_verified': True, 'backend': self.backend,
                'artifact': self.expected['artifact'], 'artifact_sha256': self.expected['artifact_sha256'],
                'slurm_job': self.expected['slurm_job'], 'performance': performance,
                'fixture_sha256': self.fixture_sha, 'binaries': dict(self.binaries),
                'files': dict(sorted(self.files.items()))}


def verify_performance_evidence(output_dir, case_dir, *, backend, expected):
    """Validate raw trials and return freshly calculated performance and hashes.

    ``expected`` contains ``slurm_job``, ``resources``, ``launcher_sha256``,
    ``artifact``, ``artifact_sha256``, ``target``, ``candidate_executable`` and
    ``baseline_executable``; PT additionally requires the site ``nsys`` path.
    No cached boolean or aggregate report can substitute for missing raw files.
    """
    return _Verifier(output_dir, case_dir, backend, expected).run()
