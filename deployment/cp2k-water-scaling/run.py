#!/usr/bin/env python3
"""Native CP2K water memory calibration and equal-CPU-budget comparisons."""
import argparse
import csv
import hashlib
import importlib.util
import itertools
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import statistics
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO/'controller'))
import cp2k_benchmark as fixtures

ROOT = Path('/home/stardust/sai-hpc-software')
SOURCE = '67b5da876dd6a76b8b021d5a04d1c81ba79a4c50'
BUILD = 'cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43'
POINTS = {'4V100': (1, 4), '16V100': (1, 4, 16)}
START = {('4V100', 1): (7, 7, 7), ('4V100', 4): (9, 9, 9),
         ('16V100', 1): (6, 6, 6), ('16V100', 4): (8, 8, 8), ('16V100', 16): (10, 10, 10)}


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')


def host_memory_fraction(request):
    value = request.get('host_memory_fraction', .92)
    require(math.isfinite(value) and .85 <= value <= .97, 'Host-memory guard must be between 85% and 97%')
    return value


def input_text(mode, shape, calibration=False, block=32):
    require(mode in ('gpu', 'cpu'), 'Unknown backend')
    require(len(shape) == 3 and all(isinstance(n, int) and 2 <= n <= 40 for n in shape), 'Bad water cell')
    text = fixtures.water_input()
    coords = []
    for x, y, z in itertools.product(*(range(n) for n in shape)):
        for atom, dx, dy in (('O', 0, 0), ('H', .9572, 0), ('H', -.239987, .927297)):
            coords.append(f'      {atom} {0.8+3.1*x+dx:.6f} {0.8+3.1*y+dy:.6f} {0.8+3.1*z:.6f}\n')
    text = text.replace(fixtures.WATER_COORDINATES, ''.join(coords))
    text = text.replace('ABC 12.0 12.0 12.0', 'ABC '+' '.join(f'{3.1*n:.1f}' for n in shape))
    text = text.replace('PERIODIC NONE', 'PERIODIC XYZ').replace('POISSON_SOLVER MT', 'POISSON_SOLVER PERIODIC')
    control = (f'  PREFERRED_DIAG_LIBRARY {"CUSOLVER" if mode == "gpu" else "SL"}\n'
               f'  DIRECT_GENERALIZED_DIAGONALIZATION {"T" if mode == "gpu" else "F"}\n'
               '  BLACS_GRID SQUARE\n'
               '  &TIMINGS\n    THRESHOLD 0\n    TIMINGS_LEVEL 1\n  &END TIMINGS\n')
    if mode == 'cpu':
        control += '  PREFERRED_DGEMM_LIBRARY BLAS\n  &GRID\n    BACKEND CPU\n  &END GRID\n'
    text = text.replace('  RUN_TYPE ENERGY_FORCE\n', '  RUN_TYPE ENERGY_FORCE\n'+control)
    start = text.index('      &OT ON\n')
    end = text.index('      &END OT\n', start)+len('      &END OT\n')
    text = text[:start]+(f'      NROW_BLOCK {block}\n      NCOL_BLOCK {block}\n'
                        '      CHOLESKY RESTORE\n      LEVEL_SHIFT 0\n'
                        '      &DIAGONALIZATION\n        ALGORITHM STANDARD\n'
                        '      &END DIAGONALIZATION\n')+text[end:]
    # Every sample starts from ATOMIC. Unused wavefunctions and backups would
    # otherwise add quadratic shared-storage traffic to these large cases.
    text = text.replace('    &END SCF\n', '      &PRINT\n        &RESTART OFF\n        &END RESTART\n'
        '        &RESTART_HISTORY OFF\n        &END RESTART_HISTORY\n      &END PRINT\n    &END SCF\n')
    if calibration:
        text = text.replace('RUN_TYPE ENERGY_FORCE', 'RUN_TYPE ENERGY')
        text = text.replace('      MAX_SCF 100', '      MAX_SCF 3\n      IGNORE_CONVERGENCE_FAILURE T')
        text = text.replace('        MAX_SCF 10', '        MAX_SCF 1')
    return text


def timers(log, mode):
    result = {}
    pattern = r'^\s*(\w+)\s+(\d+)\s+([\d.]+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+\d+)?\s*$'
    for row in re.findall(pattern, log, re.M):
        if row[0].startswith(('cp_fm_', 'eigensolver', 'qs_', 'pw_', 'grid_', 'dbcsr_')) or row[0] == 'CP2K':
            require(row[0] not in result, 'Duplicate timer')
            result[row[0]] = dict(calls=int(row[1]), seconds=fixtures.number(row[6]))
    called = lambda key: result.get(key, {}).get('calls', 0) > 0
    if mode == 'gpu':
        require(called('eigensolver_generalized') and called('cp_fm_general_cusolver'), 'Direct cuSOLVER not executed')
    else:
        require(called('eigensolver') and called('cp_fm_syevd'), 'Standard ScaLAPACK diagonalization not executed')
        require(not any('cusolver' in k or 'elpa' in k or k == 'eigensolver_generalized' for k in result),
                'Unexpected accelerated eigensolver in CPU baseline')
    return result


def gpu_inventory():
    visible = os.environ.get('CUDA_VISIBLE_DEVICES', '').split(',')
    require(visible and all(visible), 'Missing allocated GPU visibility')
    rows = subprocess.check_output(['nvidia-smi', '-i', ','.join(visible),
        '--query-gpu=uuid,memory.total,name,pci.bus_id', '--format=csv,noheader,nounits'], text=True)
    values = [dict(zip(('uuid', 'total_mib', 'name', 'pci_bus'), [x.strip() for x in row]))
              for row in csv.reader(rows.splitlines())]
    for value in values:
        value['total_mib'] = float(value['total_mib'])
    require(len(values) == len(visible) and len({v['uuid'] for v in values}) == len(values), 'Ambiguous GPUs')
    return values


def cgroup_memory():
    # Select the strictest finite cgroup limit; parent job limits include all MPI ranks.
    entries = []
    for row in Path('/proc/self/cgroup').read_text().splitlines():
        hierarchy, controllers, relative = row.split(':', 2)
        if hierarchy != '0' or controllers:
            continue
        current = Path('/sys/fs/cgroup')/relative.lstrip('/')
        while current != Path('/sys/fs'):
            maximum = current/'memory.max'
            if maximum.is_file() and maximum.read_text().strip().isdigit():
                entries.append(dict(path=str(current), limit=int(maximum.read_text()),
                                    used=int((current/'memory.current').read_text())))
            if current == Path('/sys/fs/cgroup'):
                break
            current = current.parent
    return min(entries, key=lambda v: (v['limit'], -v['used'])) if entries else None


def resource_sample(case, devices):
    rows = subprocess.check_output(['nvidia-smi', '-i', ','.join(d['uuid'] for d in devices),
        '--query-gpu=uuid,memory.used,utilization.gpu,clocks.sm,power.draw',
        '--format=csv,noheader,nounits'], text=True, timeout=15)
    gpus = {row[0].strip(): dict(zip(('memory_mib', 'util_percent', 'sm_mhz', 'power_w'),
            [float(v.strip()) if v.strip().replace('.', '', 1).isdigit() else None for v in row[1:]]))
            for row in csv.reader(rows.splitlines())}
    gpu_processes = gpu_clients(devices)
    processes = []
    for record in case.glob('rank-*.json'):
        try:
            rank = json.loads(record.read_text())
            fields = Path(f'/proc/{rank["pid"]}/stat').read_text().rsplit(')', 1)[1].split()
            processes.append(dict(rank=rank['rank'], cpu_seconds=(int(fields[11])+int(fields[12]))/os.sysconf('SC_CLK_TCK'),
                                  rss_bytes=int(fields[21])*os.sysconf('SC_PAGE_SIZE')))
        except (FileNotFoundError, ProcessLookupError, json.JSONDecodeError):
            pass
    return dict(time=time.time(), gpus=gpus, processes=processes, gpu_processes=gpu_processes, cgroup=cgroup_memory())


def gpu_clients(devices):
    compute = subprocess.check_output(['nvidia-smi', '--query-compute-apps=gpu_uuid,pid,used_gpu_memory',
        '--format=csv,noheader,nounits'], text=True, timeout=15)
    uuids = {d['uuid'] for d in devices}
    return [dict(uuid=row[0].strip(), pid=int(row[1]), used_mib=row[2].strip())
            for row in csv.reader(compute.splitlines()) if len(row) == 3 and row[0].strip() in uuids]


def await_idle_gpus(devices):
    # A terminated MPI launcher may return before CUDA contexts disappear.
    # Never count a previous calculation's allocations against the next size.
    for _ in range(30):
        clients = gpu_clients(devices)
        if not clients:
            return
        time.sleep(1)
    raise ValueError('Allocated GPUs still have compute clients: '+str(clients))


def cleanup_stopped_ranks(case):
    stopped = []
    for path in case.glob('rank-*.json'):
        record = json.loads(path.read_text())
        try:
            stat = Path(f'/proc/{record["pid"]}/stat').read_text().rsplit(')', 1)[1].split()
            if int(stat[19]) == record['start_ticks'] and stat[0] != 'Z':
                # Only this launch's recorded process, including PID reuse protection.
                os.kill(record['pid'], signal.SIGKILL)
                stopped.append(record['pid'])
        except (FileNotFoundError, ProcessLookupError):
            pass
    return stopped


def cpu_environment(binary):
    prefix = Path(binary).parents[1]
    return dict(os.environ, CUDA_VISIBLE_DEVICES='',
        LD_LIBRARY_PATH=':'.join(str(prefix/p) for p in
            ('lib64', 'dependencies/dbcsr/lib64', 'dependencies/libxs/lib'))+':'+os.environ.get('LD_LIBRARY_PATH', ''))


def verify_ranks(case, mode, gpus, ranks, pe):
    records = [json.loads(p.read_text()) for p in sorted(case.glob('rank-*.json'))]
    require(len(records) == ranks and {r['rank'] for r in records} == set(range(ranks)), 'Missing rank trace')
    used = set()
    for record in records:
        cores = {tuple(v) for v in record['cores'].values()}
        require(len(cores) == pe and not cores & used, 'Overlapping or incorrect physical-core binding')
        used |= cores
    require(len(used) == gpus*4, 'Incorrect physical CPU quota')
    assigned = [r['gpu'] for r in records]
    require((len(set(assigned)) == gpus and all(assigned)) if mode == 'gpu' else not any(assigned), 'Incorrect GPU mapping')
    return records


def run_case(root, request, devices, name, mode, shape, ranks, threads, calibration=False, block=32):
    memory_fraction = host_memory_fraction(request)
    case = root/'cases'/name
    case.mkdir()
    pe = request['gpus']*4//ranks
    require(ranks*pe == request['gpus']*4 and threads <= pe, 'CPU oversubscription')
    for filename in fixtures.DATA_HASHES:
        shutil.copyfile(root/'fixtures'/filename, case/filename)
    (case/'input.inp').write_text(input_text(mode, shape, calibration, block))
    binary = request['gpu_binary'] if mode == 'gpu' else request['cpu_binary']
    env = dict(os.environ, WATER_MODE=mode, WATER_GPU_UUIDS=json.dumps([d['uuid'] for d in devices]),
               WATER_CORES_PER_RANK=str(pe), WATER_BINARY=binary, OMP_NUM_THREADS=str(threads),
               OPENBLAS_NUM_THREADS=str(threads), OMP_PLACES='cores', OMP_PROC_BIND='close',
               OMP_DYNAMIC='FALSE', OMP_MAX_ACTIVE_LEVELS='1')
    if mode == 'cpu':
        env['LD_LIBRARY_PATH'] = cpu_environment(binary)['LD_LIBRARY_PATH']
    argv = ['mpirun', '-np', str(ranks), '--map-by', f'ppr:{ranks}:node:pe={pe}', '--bind-to', 'core',
            '--report-bindings', 'python3', str(Path(__file__).with_name('rank.py')), '-i', 'input.inp']
    write_json(case/'launch.json', dict(argv=argv, mode=mode, shape=shape, ranks=ranks, threads=threads,
                                      physical_cores=ranks*pe, calibration=calibration, block=block))
    await_idle_gpus(devices)
    print(f'START {name}: {math.prod(shape)} waters, {ranks} MPI x {threads} OMP, {mode}', flush=True)
    peaks = {d['uuid']: 0.0 for d in devices}
    samples = []
    stop = None
    started = time.monotonic()
    with (case/'cp2k.log').open('w') as output, (case/'resources.jsonl').open('w') as stream:
        process = subprocess.Popen(argv, cwd=case, env=env, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while process.poll() is None:
                sample = resource_sample(case, devices)
                samples.append(sample)
                stream.write(json.dumps(sample, sort_keys=True)+'\n'); stream.flush()
                for device in devices:
                    memory = sample['gpus'][device['uuid']]['memory_mib']
                    require(memory is not None, 'Missing GPU memory measurement')
                    peaks[device['uuid']] = max(peaks[device['uuid']], memory)
                if sample['cgroup'] and sample['cgroup']['used'] > memory_fraction*sample['cgroup']['limit']:
                    stop = 'host_memory_limit'
                if request['stage'] == 'calibrate' and any(peaks[d['uuid']] > .86*d['total_mib'] for d in devices):
                    stop = 'gpu_memory_guard'
                if time.monotonic()-started > request['case_seconds']:
                    stop = 'case_timeout'
                if stop:
                    os.killpg(process.pid, signal.SIGTERM)
                    break
                time.sleep(1)
            try:
                code = process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                code = process.wait(timeout=20)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=20)
            if stop:
                write_json(case/'stopped-ranks.json', cleanup_stopped_ranks(case))
    elapsed = time.monotonic()-started
    (case/'elapsed.txt').write_text(str(elapsed)+'\n')
    result = dict(name=name, mode=mode, shape=shape, waters=math.prod(shape), basis_functions=23*math.prod(shape),
                  ranks=ranks, threads=threads, block=block, calibration=calibration, returncode=code, stop=stop,
                  peak_gpu_mib=peaks, peak_gpu_fraction={d['uuid']: peaks[d['uuid']]/d['total_mib'] for d in devices},
                  elapsed_seconds=elapsed, science_accepted=False, host_memory_fraction=memory_fraction)
    cgroups = [s['cgroup'] for s in samples if s['cgroup']]
    result['host_memory'] = max(cgroups, key=lambda c: c['used']) if cgroups else None
    result['sampled_cpu_seconds'] = sum(max((p['cpu_seconds'] for s in samples for p in s['processes'] if p['rank'] == r), default=0)
                                         for r in range(ranks))
    result['average_physical_core_utilization'] = result['sampled_cpu_seconds']/elapsed/(4*request['gpus'])
    write_json(case/'sample.json', result)  # Preserve diagnostics even on a parser/backend failure.
    if code == 0 and not stop:
        log = (case/'cp2k.log').read_text()
        require('PROGRAM ENDED AT' in log and not re.search(r'\bABORT\b', log), 'Incomplete CP2K run')
        result['timers'] = timers(log, mode)
        result['bindings'] = verify_ranks(case, mode, request['gpus'], ranks, pe)
        if mode == 'cpu':
            pids = {r['pid'] for r in result['bindings']}
            require(not any(p['pid'] in pids for s in samples for p in s['gpu_processes']), 'CPU calculation used a GPU')
            result['no_gpu_compute_process_verified'] = True
        if not calibration:
            fixtures.CASES['scaling'] = dict(elements=['O', 'H', 'H']*math.prod(shape))
            result.update(fixtures.parse_science(log, (case/'forces.xyz').read_text(), str(elapsed), 'scaling'))
            result['science_accepted'] = True
        result['scf_steps'] = [int(v) for v in re.findall(r'SCF run converged in\s+(\d+) steps', log)]
    write_json(case/'sample.json', result)
    print(f'FINISH {name}: rc={code}, stop={stop}, GPU fractions={list(result["peak_gpu_fraction"].values())}', flush=True)
    return result


def next_shape(waters, lower, upper, tried):
    candidates = {(x, y, z) for x in range(3, 41) for y in range(x, min(int(1.5*x)+1, 41))
                  for z in range(y, min(int(1.5*x)+1, 41)) if lower < x*y*z < upper and (x, y, z) not in tried}
    return min(candidates, key=lambda s: abs(math.log(math.prod(s)/waters))) if candidates else None


def calibrate(root, request, devices):
    shape = tuple(request['shape'])
    tried, results = set(), []
    lower, upper = 0, 40**3+1
    selected = None
    reason = 'probe_limit'
    limiting_resource = 'host_memory_limit' if request.get('prior_host_limit') else None
    for attempt in range(request['probes']):
        tried.add(shape)
        sample = run_case(root, request, devices, f'calibration-{attempt}', 'gpu', shape, request['gpus'], 4,
                          not request.get('full_calibration', False))
        results.append(sample)
        maximum = max(sample['peak_gpu_fraction'].values())
        minimum = min(sample['peak_gpu_fraction'].values())
        completed = sample['returncode'] == 0 and sample['stop'] is None and 'timers' in sample
        if completed and minimum >= .60 and maximum <= .75:
            selected, reason = sample, 'all_gpus_in_target'
            break
        if completed and maximum <= .75:
            selected = sample
            lower = max(lower, sample['waters'])
            target = sample['waters']*min(1.5, max(1.12, math.sqrt(.675/max(maximum, .01))))
        elif not completed and sample['stop'] not in ('host_memory_limit', 'gpu_memory_guard'):
            reason = 'execution_failed'
            break
        else:
            limiting_resource = sample['stop'] or 'gpu_memory_target'
            upper = min(upper, sample['waters'])
            target = sample['waters']*.8
        if upper < 40**3+1:
            target = (lower+upper)/2 if lower else .9*upper
        shape = next_shape(target, lower, upper, tried)
        write_json(root/'results/calibration.json', dict(selected=selected, reason='in_progress', probes=results))
        if shape is None:
            reason = 'discrete_size_or_memory_limit'
            break
    report = dict(selected=selected, reason=reason, probes=results, performance_result=False,
                  limiting_resource=limiting_resource, full_calibration=request.get('full_calibration', False),
                  target_fraction=[.60, .75], scope=('complete ENERGY_FORCE memory calibration' if request.get('full_calibration')
                    else '3 SCF steps, memory calibration only; full-run peak must be checked again'))
    write_json(root/'results/calibration.json', report)
    require(selected is not None, 'No completed safe calibration point; inspect raw evidence')


def tuning_trials(gpus):
    # CPU layouts all consume the full physical-core allocation. GPU uses one distinct card per rank.
    return ([('gpu', gpus, t, b) for b in (32, 64) for t in (4, 2, 1)] +
            [('cpu', gpus*m, 4//m, 32) for m in (1, 2, 4)])


def read_tuning(root, request, expected=None):
    """Reuse only nine independently checked tuning cases, never old timing pairs."""
    root = Path(root)
    if expected:
        require(str(root) == expected['path'], 'Different tuning source')
        for name, value in expected['files'].items():
            require(digest(root/name) == value, 'Imported tuning evidence changed: '+name)
    source = json.loads((root/'request.json').read_text())
    require(source['stage'] == 'compare' and not source.get('tuning_import'), 'Use the original full tuning run')
    identity = ('partition', 'gpus', 'physical_cores', 'shape', 'source_sha',
                'gpu_binary_sha256', 'cpu_binary_sha256', 'install_receipt_sha256')
    require(all(source[k] == request[k] for k in identity), 'Tuning identity differs')
    job = (root/'job.id').read_text().strip()
    accounting = subprocess.check_output(['sacct', '-X', '-j', job, '-n', '-P',
        '-o', 'JobID,State,ExitCode,Elapsed,NodeList'], text=True, timeout=30)
    rows = [line.strip().split('|') for line in accounting.splitlines() if line.strip()]
    require(len(rows) == 1 and rows[0][0] == job and rows[0][1].split()[0] in
            ('COMPLETED', 'FAILED', 'TIMEOUT', 'CANCELLED', 'OUT_OF_MEMORY'), 'Tuning source is not terminal')
    hashes = {name: digest(root/name) for name in source['inputs']}
    require(hashes == source['inputs'], 'Frozen tuning source changed')
    for name in ('request.json', 'job.id', 'results/tuning.json', 'results/devices.json'):
        hashes[name] = digest(root/name)
    spec = importlib.util.spec_from_file_location('frozen_tuning_protocol',
        root/'control/deployment/cp2k-water-scaling/run.py')
    protocol = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(protocol)
    devices = json.loads((root/'results/devices.json').read_text())
    samples = json.loads((root/'results/tuning.json').read_text())
    trials = tuning_trials(request['gpus'])
    require(len(samples) == len(trials), 'Incomplete tuning schedule')
    for i, (sample, (mode, ranks, threads, block)) in enumerate(zip(samples, trials)):
        name = f'tune-{i}-{mode}'
        require(sample['name'] == name, 'Wrong tuning case order')
        case = root/'cases'/name
        launch = json.loads((case/'launch.json').read_text())
        layout = dict(mode=mode, ranks=ranks, threads=threads, block=block,
                      shape=request['shape'], physical_cores=request['physical_cores'], calibration=False)
        require(all(launch[k] == v and (k not in sample or sample[k] == v) for k, v in layout.items()),
                'Wrong tuning layout')
        raw = json.loads((case/'sample.json').read_text())
        require(all(sample.get(k) == v for k, v in raw.items()), 'Changed tuning summary')
        require(raw['science_accepted'] and raw['returncode'] == 0 and raw['stop'] is None,
                'Unaccepted tuning case')
        require((case/'input.inp').read_text() == protocol.input_text(mode, request['shape'], False, block),
                'Changed tuning scientific input')
        fixtures.CASES['scaling'] = dict(elements=['O', 'H', 'H']*math.prod(request['shape']))
        actual = fixtures.parse_science((case/'cp2k.log').read_text(), (case/'forces.xyz').read_text(),
                                        (case/'elapsed.txt').read_text(), 'scaling')
        require(all(actual[k] == sample[k] for k in actual), 'Tuning science differs from raw evidence')
        require(timers((case/'cp2k.log').read_text(), mode) == sample['timers'], 'Changed tuning backend timers')
        resource = [json.loads(line) for line in (case/'resources.jsonl').read_text().splitlines()]
        peaks = {d['uuid']: max(s['gpus'][d['uuid']]['memory_mib'] for s in resource) for d in devices}
        require(peaks == sample['peak_gpu_mib'], 'Changed tuning memory summary')
        bindings = verify_ranks(case, mode, request['gpus'], ranks, request['physical_cores']//ranks)
        require(all(r['omp_threads'] == threads for r in bindings), 'Wrong tuning OpenMP count')
        if mode == 'cpu':
            pids = {r['pid'] for r in bindings}
            require(not any(p['pid'] in pids for s in resource for p in s['gpu_processes']), 'Tuning CPU used GPU')
        require(fixtures.compare_science(sample, samples[0]) == sample['comparison'], 'Changed tuning agreement')
        for path in case.iterdir():
            if path.is_file():
                hashes[str(path.relative_to(root))] = digest(path)
    manifest = dict(path=str(root), job=job, scheduler_state=rows[0][1], files=hashes,
                    completed_tuning_cases=len(samples), performance_result=False)
    if expected:
        require(manifest == expected, 'Tuning provenance changed')
    return samples, manifest


def compare(root, request, devices):
    shape, gpus = request['shape'], request['gpus']
    samples = []
    reference = None
    if request.get('tuning_import'):
        samples, manifest = read_tuning(request['tuning_import']['path'], request, request['tuning_import'])
        reference = samples[0]
        write_json(root/'results/imported-tuning.json', manifest)
        write_json(root/'results/tuning.json', samples)
        print(f'Reused {len(samples)} validated tuning cases; all warm-up and measured pairs are new', flush=True)
    for i, (mode, ranks, threads, block) in enumerate([] if samples else tuning_trials(gpus)):
        sample = run_case(root, request, devices, f'tune-{i}-{mode}', mode, shape, ranks, threads, block=block)
        require(sample['science_accepted'], 'Tuning calculation failed')
        if reference is None:
            reference = sample
        sample['comparison'] = fixtures.compare_science(sample, reference)
        samples.append(sample)
        write_json(root/'results/tuning.json', samples)
    winners = {mode: min((s for s in samples if s['mode'] == mode), key=lambda s: s['cp2k_seconds']) for mode in ('gpu', 'cpu')}
    measured = []
    for repeat in range(4):  # First pair is an excluded warm-up after selecting the layouts.
        for mode in (('gpu', 'cpu') if repeat % 2 == 0 else ('cpu', 'gpu')):
            choice = winners[mode]
            sample = run_case(root, request, devices, f'pair-{repeat}-{mode}', mode, shape,
                              choice['ranks'], choice['threads'], block=choice['block'])
            require(sample['science_accepted'], 'Measured calculation failed')
            sample['comparison'] = fixtures.compare_science(sample, reference)
            sample['warmup'] = repeat == 0
            measured.append(sample)
            write_json(root/'results/pairs.json', measured)
    medians = {mode: statistics.median(s['cp2k_seconds'] for s in measured if s['mode'] == mode and not s['warmup']) for mode in winners}
    write_json(root/'results/comparison.json', dict(request=request, selected_layouts=winners, samples=measured,
        median_seconds=medians, cpu_over_gpu=medians['cpu']/medians['gpu'], scope='best among measured layouts; synthetic PBE water, standard diagonalization'))


def smoke(root, request, devices):
    gpus = request['gpus']
    reference = run_case(root, request, devices, 'smoke-gpu', 'gpu', request['shape'], gpus, 4)
    require(reference['science_accepted'], 'GPU smoke failed')
    samples = [reference]
    for multiplier in (1, 2, 4):
        sample = run_case(root, request, devices, f'smoke-cpu-{multiplier}', 'cpu', request['shape'],
                          gpus*multiplier, 4//multiplier)
        require(sample['science_accepted'], 'CPU smoke failed')
        sample['comparison'] = fixtures.compare_science(sample, reference)
        samples.append(sample)
    write_json(root/'results/smoke.json', dict(accepted=True, request=request, samples=samples,
                                              performance_result=False))


def describe_cpu(root, request):
    binary = request['cpu_binary']
    prefix = Path(binary).parents[1]
    cache = (prefix/'share/sai/CMakeCache.txt').read_text()
    require(re.search(r'^CP2K_USE_ACCEL:STRING=NONE$', cache, re.M), 'CPU build enables acceleration')
    require((prefix/'share/sai/source-sha').read_text().strip() == SOURCE, 'CPU source differs')
    for mode, executable in (('cpu', binary), ('gpu', request['gpu_binary'])):
        env = cpu_environment(binary) if mode == 'cpu' else dict(os.environ)
        env.update(OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
        version = subprocess.check_output([executable, '--version'], env=env, text=True, stderr=subprocess.STDOUT, timeout=120)
        (root/'results'/f'{mode}-version.txt').write_text(version)
        parsed = fixtures.parse_version(version)
        require('2026.2' in parsed['version'] and SOURCE[:10] in parsed['revision'], 'Wrong CP2K version/source')
        if mode == 'cpu':
            require(not {'offload_cuda', 'dbcsr_acc', 'cusolvermp'} & set(parsed['flags']), 'CPU binary has GPU features')
        linked = subprocess.check_output(['ldd', executable], env=env, text=True, stderr=subprocess.STDOUT)
        (root/'results'/f'{mode}-ldd.txt').write_text(linked)
        require('not found' not in linked, 'Missing native library')
        if mode == 'cpu':
            require(not re.search(r'lib(?:cusolver|cublas|cufft|elpa|spla|cosma)', linked), 'CPU binary loads GPU math libraries')


def verify(root):
    """Reparse raw science/resource evidence only after successful scheduler completion."""
    request = json.loads((root/'request.json').read_text())
    job = (root/'job.id').read_text().strip()
    accounting = subprocess.check_output(['sacct', '-X', '-j', job, '-n', '-P',
        '-o', 'JobID,State,ExitCode,Elapsed,NodeList'], text=True)
    rows = [line.strip().split('|') for line in accounting.splitlines() if line.strip()]
    require(len(rows) == 1 and rows[0][:3] == [job, 'COMPLETED', '0:0'], 'Job did not complete successfully')
    (root/'results/accounting.txt').write_text(accounting)
    for name, expected in request['inputs'].items():
        require(digest(root/name) == expected, 'Frozen input changed: '+name)
    frozen = root/'control/deployment/cp2k-water-scaling/run.py'
    spec = importlib.util.spec_from_file_location('frozen_water_protocol', frozen)
    protocol = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(protocol)
    devices = json.loads((root/'results/devices.json').read_text())
    samples, parsed = {}, {}
    for case in sorted((root/'cases').iterdir()):
        launch = json.loads((case/'launch.json').read_text())
        sample = json.loads((case/'sample.json').read_text())
        samples[case.name] = sample
        require((case/'input.inp').read_text() == protocol.input_text(launch['mode'], launch['shape'],
                launch['calibration'], launch['block']), 'Scientific input differs from protocol')
        resource = [json.loads(line) for line in (case/'resources.jsonl').read_text().splitlines()]
        peaks = {d['uuid']: max(s['gpus'][d['uuid']]['memory_mib'] for s in resource) for d in devices}
        require(peaks == sample['peak_gpu_mib'], 'Memory summary differs from samples')
        if launch['calibration']:
            if sample['returncode'] == 0 and sample['stop'] is None:
                require(timers((case/'cp2k.log').read_text(), 'gpu') == sample['timers'], 'Changed calibration timers')
            continue
        if request['stage'] == 'calibrate' and (sample['returncode'] != 0 or sample['stop']):
            continue
        require(sample['returncode'] == 0 and sample['stop'] is None, 'Failed science case')
        fixtures.CASES['scaling'] = dict(elements=['O', 'H', 'H']*math.prod(launch['shape']))
        actual = fixtures.parse_science((case/'cp2k.log').read_text(), (case/'forces.xyz').read_text(),
                                        (case/'elapsed.txt').read_text(), 'scaling')
        require(all(actual[k] == sample[k] for k in actual), 'Science summary differs from raw files')
        require(timers((case/'cp2k.log').read_text(), launch['mode']) == sample['timers'], 'Changed backend timers')
        ranks = verify_ranks(case, launch['mode'], request['gpus'], launch['ranks'],
                             request['physical_cores']//launch['ranks'])
        require(all(r['omp_threads'] == launch['threads'] for r in ranks), 'Wrong OpenMP count')
        if launch['mode'] == 'cpu':
            pids = {r['pid'] for r in ranks}
            require(not any(p['pid'] in pids for s in resource for p in s['gpu_processes']), 'CPU used GPU')
        parsed[case.name] = actual
    if request['stage'] == 'calibrate':
        report = json.loads((root/'results/calibration.json').read_text())
        require(report['selected'] == samples[report['selected']['name']], 'Changed calibration selection')
    else:
        imported = []
        if request.get('tuning_import'):
            require(request['stage'] == 'compare', 'Only comparisons can reuse tuning')
            imported, _ = read_tuning(request['tuning_import']['path'], request, request['tuning_import'])
            require(json.loads((root/'results/tuning.json').read_text()) == imported, 'Changed imported tuning')
            require(set(parsed) == {f'pair-{r}-{mode}' for r in range(4) for mode in ('cpu', 'gpu')},
                    'All timing pairs must be freshly completed in this allocation')
        require(len(parsed)+len(imported) == (4 if request['stage'] == 'smoke' else 17), 'Incomplete benchmark schedule')
        reference = next(iter(parsed.values()))
        for sample in [*parsed.values(), *imported]:
            fixtures.compare_science(sample, reference)
        if request['stage'] == 'compare':
            report = json.loads((root/'results/comparison.json').read_text())
            if imported:
                winners = {mode: min((s for s in imported if s['mode'] == mode), key=lambda s: s['cp2k_seconds'])
                           for mode in ('gpu', 'cpu')}
                require(report['selected_layouts'] == winners, 'Changed layout selection')
                for case_name, sample in samples.items():
                    choice = winners[sample['mode']]
                    launch = json.loads((root/'cases'/case_name/'launch.json').read_text())
                    require(all(launch[k] == choice[k] for k in ('mode', 'ranks', 'threads', 'block')),
                            'Timing pair did not use the selected layout')
            medians = {mode: statistics.median(parsed[f'pair-{r}-{mode}']['cp2k_seconds'] for r in (1, 2, 3))
                       for mode in ('cpu', 'gpu')}
            require(report['median_seconds'] == medians and report['cpu_over_gpu'] == medians['cpu']/medians['gpu'],
                    'Changed performance summary')
    hashes = {str(p.relative_to(root)): digest(p) for folder in ('cases', 'fixtures', 'results')
              for p in (root/folder).rglob('*') if p.is_file() and p.name != 'verified.json' and
              not p.name.startswith('slurm-')}
    result = dict(verified=True, request_sha256=digest(root/'request.json'), job=job,
                  stage=request['stage'], completed_cases=len(parsed), files=hashes)
    write_json(root/'results/verified.json', result)
    print(json.dumps({k: v for k, v in result.items() if k != 'files'}))
    return result


def submit(args):
    require(args.gpus in POINTS[args.partition], 'Unsupported resource point')
    require(re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,80}', args.name), 'Unsafe run name')
    host_memory_fraction({'host_memory_fraction': args.host_memory_fraction})
    require(not args.tuning_run or args.stage == 'compare', 'Tuning reuse requires a comparison')
    root = ROOT/'runtime-tests'/('cp2k-water-'+args.name)
    root.mkdir()
    for folder in ('results', 'cases', 'fixtures', 'control', 'runtime'):
        (root/folder).mkdir()
    shutil.copytree(REPO/'deployment/cp2k-water-scaling', root/'control/deployment/cp2k-water-scaling',
                    ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(REPO/'controller', root/'control/controller', ignore=shutil.ignore_patterns('__pycache__'))
    prefix = Path('/opt/apps/cp2k')/BUILD/args.partition
    for filename, expected in fixtures.DATA_HASHES.items():
        source = prefix/'share/cp2k/data'/filename
        require(digest(source) == expected, 'Changed basis/potential data')
        shutil.copyfile(source, root/'fixtures'/filename)
    require((prefix/'share/sai/source-sha').read_text().strip() == SOURCE, 'Wrong installed source')
    binary = prefix/'bin/cp2k.psmp'
    shape = args.shape or ([4, 4, 4] if args.stage == 'smoke' else START[(args.partition, args.gpus)])
    request = dict(partition=args.partition, gpus=args.gpus, physical_cores=4*args.gpus, stage=args.stage,
        shape=shape, probes=args.probes, case_seconds=args.case_seconds, full_calibration=args.full_calibration,
        host_memory_fraction=args.host_memory_fraction,
        source_sha=SOURCE, gpu_binary=str(binary), gpu_binary_sha256=digest(binary),
        cpu_binary=args.cpu_binary, install_receipt_sha256=digest(prefix/'share/sai/deployment.json'))
    if args.host_limit_run:
        require(args.stage == 'calibrate' and args.full_calibration, 'Prior RAM limits require complete calibration')
        previous = Path(args.host_limit_run)
        prior_request = json.loads((previous/'request.json').read_text())
        require(all(prior_request[k] == request[k] for k in ('partition', 'gpus', 'gpu_binary_sha256')),
                'Prior host-memory limit used different resources or executable')
        limits = []
        for sample_path in (previous/'cases').glob('*/sample.json'):
            sample = json.loads(sample_path.read_text())
            memory = sample.get('host_memory') or {}
            if (sample.get('stop') == 'host_memory_limit' and sample['waters'] > math.prod(shape) and
                    memory.get('used', 0) >= .92*memory.get('limit', math.inf)):
                limits.append(dict(path=str(sample_path), sha256=digest(sample_path), waters=sample['waters'], memory=memory))
        require(limits, 'No larger run with an observed host-memory limit')
        request['prior_host_limit'] = dict(run=str(previous), request_sha256=digest(previous/'request.json'),
                                          observations=limits)
    if args.stage != 'calibrate':
        require(args.cpu_binary, 'Native CPU binary is required')
        require((Path(args.cpu_binary).parents[2]/'results/build-complete').is_file(), 'CPU build is incomplete')
        request['cpu_binary_sha256'] = digest(args.cpu_binary)
    if args.stage == 'compare':
        require(args.calibration_run and args.cpu_smoke_run, 'Comparison needs calibration and CPU numerical acceptance')
        calibration = Path(args.calibration_run)
        proof = json.loads((calibration/'results/calibration.json').read_text())
        previous = json.loads((calibration/'request.json').read_text())
        require(proof['selected'] and list(proof['selected']['shape']) == list(shape), 'Size differs from calibration')
        require(all(previous[k] == request[k] for k in ('partition', 'gpus', 'gpu_binary_sha256')), 'Calibration identity differs')
        smoke_path = Path(args.cpu_smoke_run)/'results/smoke.json'
        verify(Path(args.cpu_smoke_run))
        accepted = json.loads(smoke_path.read_text())
        require(accepted['accepted'] and all(accepted['request'][k] == request[k] for k in
                ('partition', 'cpu_binary_sha256', 'gpu_binary_sha256')), 'CPU acceptance differs')
        request['calibration'] = dict(path=str(calibration), sha256=digest(calibration/'results/calibration.json'), reason=proof['reason'])
        request['cpu_acceptance'] = dict(path=str(smoke_path), sha256=digest(smoke_path))
        if args.tuning_run:
            _, request['tuning_import'] = read_tuning(Path(args.tuning_run).resolve(), request)
    request['inputs'] = {str(p.relative_to(root)): digest(p) for f in ('control', 'fixtures')
                         for p in (root/f).rglob('*') if p.is_file()}
    write_json(root/'request.json', request)
    qos = ('rush-1o2gpu' if args.partition == '4V100' else 'flood-1o2gpu') if args.gpus == 1 else (
        'huge-gpu' if args.gpus == 16 else 'rush-gpu')
    require(not (args.partition == '16V100' and args.gpus == 1 and args.minutes > 240),
            '16V100 single-GPU QOS permits at most four hours')
    command = ['sbatch', '--parsable', '--partition='+args.partition, '--qos='+qos,
        '--nodes=1', '--ntasks='+str(args.gpus if args.stage == 'calibrate' else args.gpus*4),
        '--gpus-per-node='+str(args.gpus),
        '--time='+str(args.minutes), '--job-name=cp2k-water-'+args.name,
        '--output='+str(root/'results/slurm-%j.log'), str(root/'control/deployment/cp2k-water-scaling/job.sbatch'), str(root)]
    job = subprocess.check_output(command, text=True).strip()
    require(job.isdigit(), 'Unexpected scheduler response')
    (root/'job.id').write_text(job+'\n')
    print(json.dumps(dict(job=job, run=str(root), stage=args.stage, partition=args.partition, gpus=args.gpus)))


def execute(root):
    request = json.loads((root/'request.json').read_text())
    for name, expected in request['inputs'].items():
        require(digest(root/name) == expected, 'Frozen input changed: '+name)
    require(os.environ['SLURM_JOB_PARTITION'] == request['partition'], 'Wrong partition')
    require(digest(request['gpu_binary']) == request['gpu_binary_sha256'], 'Installed GPU binary changed')
    if request['stage'] != 'calibrate':
        require(digest(request['cpu_binary']) == request['cpu_binary_sha256'], 'CPU binary changed')
        describe_cpu(root, request)
    devices = gpu_inventory()
    require(len(devices) == request['gpus'], 'Wrong GPU allocation')
    write_json(root/'results/devices.json', devices)
    write_json(root/'results/environment.json', {k: v for k, v in os.environ.items() if k.startswith(('OMP_', 'OPENBLAS_', 'NCCL_', 'CUDA_', 'SLURM_', 'OMPI_', 'PRTE_'))})
    {'calibrate': calibrate, 'compare': compare, 'smoke': smoke}[request['stage']](root, request, devices)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('submit')
    p.add_argument('--name', required=True)
    p.add_argument('--partition', choices=POINTS, required=True)
    p.add_argument('--gpus', type=int, required=True)
    p.add_argument('--stage', choices=('calibrate', 'compare', 'smoke'), default='calibrate')
    p.add_argument('--shape', nargs=3, type=int)
    p.add_argument('--cpu-binary')
    p.add_argument('--calibration-run')
    p.add_argument('--cpu-smoke-run')
    p.add_argument('--probes', type=int, default=6)
    p.add_argument('--full-calibration', action='store_true', help='Converge ENERGY_FORCE to include late SCF/force memory')
    p.add_argument('--host-limit-run', help='Existing larger run with measured RAM exhaustion in the same allocation')
    p.add_argument('--host-memory-fraction', type=float, default=.92,
                   help='Recorded early-stop fraction of the actual cgroup RAM limit (0.85 to 0.97)')
    p.add_argument('--tuning-run', help='Reuse nine validated tuning cases; all four timing pairs run afresh')
    p.add_argument('--case-seconds', type=int, default=1800)
    p.add_argument('--minutes', type=int, default=180)
    p = sub.add_parser('execute'); p.add_argument('root', type=Path)
    p = sub.add_parser('verify'); p.add_argument('root', type=Path)
    args = parser.parse_args()
    if args.command == 'submit':
        submit(args)
    elif args.command == 'execute':
        execute(args.root)
    else:
        verify(args.root)


if __name__ == '__main__':
    main()
