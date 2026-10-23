#!/usr/bin/env python3
"""Paired CP2K MPI participation and timing tests for the one-object CPU repair."""
import argparse
import contextlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys

import run as water


def trace_proof(case, ranks, order, expected):
    by_rank = {}
    for path in case.glob('scalapack-rank-*.jsonl'):
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        water.require(rows, 'Empty ScaLAPACK trace')
        rank = rows[0]['rank']
        water.require(path.name == f'scalapack-rank-{rank}.jsonl' and rank not in by_rank, 'Invalid rank trace')
        water.require([r['sequence'] for r in rows] == list(range(len(rows))), 'Missing ScaLAPACK call')
        for row in rows:
            water.require(row['rank'] == rank and 0 <= rank < ranks and row['world_size'] == ranks and
                          row['n'] == order and row['info'] == 0, 'Wrong or failed ScaLAPACK call')
            water.require(row['grid_rows'] * row['grid_cols'] == expected and
                          0 <= row['row'] < row['grid_rows'] and 0 <= row['col'] < row['grid_cols'],
                          'Unexpected BLACS participation')
        by_rank[rank] = rows
    water.require(len(by_rank) == expected, 'Wrong number of solving MPI ranks')
    counts = {rank: sum(not row['query'] for row in rows) for rank, rows in by_rank.items()}
    water.require(len(set(counts.values())) == 1 and min(counts.values()) >= 3,
                  'Repeated solves did not keep the same rank participation')
    for rows in by_rank.values():
        water.require(sum(row['query'] for row in rows) == sum(not row['query'] for row in rows), 'Missing workspace query')
    # Every solve must use a complete grid, with no duplicate BLACS coordinates.
    actual = {rank: [r for r in rows if not r['query']] for rank, rows in by_rank.items()}
    for i in range(next(iter(counts.values()))):
        water.require(len({(rows[i]['row'], rows[i]['col']) for rows in actual.values()}) == expected,
                      'Duplicate BLACS coordinates')
    return dict(world_ranks=ranks, solving_ranks=sorted(by_rank), solving_count=expected,
                matrix_order=order, repeated_solve_count=next(iter(counts.values())))


def binaries(request):
    build = Path(request['build'])
    return {'before': build/'before/bin/cp2k.psmp', 'after': build/'install/bin/cp2k.psmp'}


def check_identity(root, request):
    for name, value in request['inputs'].items():
        water.require(water.digest(root/name) == value, 'Frozen test input changed: '+name)
    for variant, binary in binaries(request).items():
        water.require(water.digest(binary) == request['binaries'][variant]['executable'], 'Executable changed')
        water.require(water.cpu_library_digest(binary) == request['binaries'][variant]['library'], 'CPU library changed')
    water.require(water.digest(Path(request['build'])/'install/lib64/trace-scalapack.so') == request['tracer_sha256'], 'Tracer changed')


def schedule(request):
    for multiplier in (1, 2, 4):
        for variant in ('before', 'after'):
            yield f'trace-{multiplier}-{variant}', variant, True, request['trace_shape'], multiplier*request['gpus'], 4//multiplier
    for repeat in range(4):
        for variant in (('before', 'after') if repeat % 2 == 0 else ('after', 'before')):
            yield f'pair-{repeat}-{variant}', variant, False, request['shape'], 4*request['gpus'], 1


@contextlib.contextmanager
def trace_environment(root, request, name, enabled):
    old = {key: os.environ.get(key) for key in ('LD_PRELOAD', 'SAI_SCALAPACK_TRACE')}
    try:
        if enabled:
            os.environ['LD_PRELOAD'] = str(Path(request['build'])/'install/lib64/trace-scalapack.so')
            os.environ['SAI_SCALAPACK_TRACE'] = str(root/'cases'/name)
        else:
            os.environ.pop('LD_PRELOAD', None); os.environ.pop('SAI_SCALAPACK_TRACE', None)
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def execute(root):
    request = json.loads((root/'request.json').read_text())
    check_identity(root, request)
    devices = water.gpu_inventory()
    water.require(os.environ['SLURM_JOB_PARTITION'] == request['partition'] and len(devices) == request['gpus'], 'Wrong allocation')
    water.write_json(root/'results/devices.json', devices)
    for variant, binary in binaries(request).items():
        linked = subprocess.check_output(['ldd', str(binary)], env=water.cpu_environment(binary), text=True)
        water.require(str(binary.parents[1]/'lib64/libcp2k.so.2026.2') in linked and 'not found' not in linked,
                      'Wrong dynamically loaded CP2K library')
        (root/'results'/f'ldd-{variant}.txt').write_text(linked)
    results, traces = {}, {}
    for name, variant, tracing, shape, ranks, threads in schedule(request):
        case_request = dict(request, cpu_binary=str(binaries(request)[variant]))
        with trace_environment(root, request, name, tracing):
            result = water.run_case(root, case_request, devices, name, 'cpu', shape, ranks, threads)
        water.require(result['science_accepted'], 'A/B calculation failed: '+name)
        results[name] = result
        if tracing:
            expected = max(1, ranks//2) if variant == 'before' else ranks
            traces[name] = trace_proof(root/'cases'/name, ranks, math.prod(shape)*23, expected)
            water.write_json(root/'results/mpi-progress.json', traces)
        else:
            water.require(not list((root/'cases'/name).glob('scalapack-*.jsonl')), 'Tracer used in timing case')
        peer = name.rsplit('-', 1)[0]+'-'+('after' if variant == 'before' else 'before')
        if peer in results:
            comparison = water.fixtures.compare_science(result, results[peer])
            print(json.dumps(dict(pair=name.rsplit('-', 1)[0], science=comparison)), flush=True)
    verify(root, scheduler=False)


def verify(root, scheduler=True):
    request = json.loads((root/'request.json').read_text())
    check_identity(root, request)
    job = (root/'job.id').read_text().strip()
    if scheduler:
        accounting = subprocess.check_output(['sacct', '-X', '-j', job, '-n', '-P', '-o', 'JobID,State,ExitCode,Elapsed,NodeList'], text=True)
        rows = [line.strip().split('|') for line in accounting.splitlines() if line.strip()]
        water.require(len(rows) == 1 and rows[0][:3] == [job, 'COMPLETED', '0:0'], 'MPI retest did not complete successfully')
        (root/'results/accounting.txt').write_text(accounting)
    samples, traces = {}, {}
    for name, variant, tracing, shape, ranks, threads in schedule(request):
        case = root/'cases'/name
        water.require((case/'input.inp').read_text() == water.input_text('cpu', shape), 'A/B input changed')
        launch = json.loads((case/'launch.json').read_text())
        water.require(launch['ranks'] == ranks and launch['threads'] == threads and launch['mode'] == 'cpu' and
                      launch['shape'] == shape and not launch['calibration'], 'A/B layout differs')
        records = water.verify_ranks(case, 'cpu', request['gpus'], ranks, 4*request['gpus']//ranks)
        water.require(all(r['omp_threads'] == threads and r['binary'] == str(binaries(request)[variant]) for r in records), 'Wrong rank binary or threads')
        sample = json.loads((case/'sample.json').read_text())
        water.require(sample['returncode'] == 0 and sample['stop'] is None, 'Incomplete A/B case')
        log = (case/'cp2k.log').read_text()
        water.fixtures.CASES['scaling'] = dict(elements=['O','H','H']*math.prod(shape))
        actual = water.fixtures.parse_science(log, (case/'forces.xyz').read_text(), (case/'elapsed.txt').read_text(), 'scaling')
        water.require(all(actual[k] == sample[k] for k in actual), 'Changed numerical summary')
        water.require(water.timers(log, 'cpu') == sample['timers'], 'Changed solver timers')
        pids = {r['pid'] for r in records}
        resources = [json.loads(line) for line in (case/'resources.jsonl').read_text().splitlines()]
        water.require(not any(p['pid'] in pids for s in resources for p in s['gpu_processes']), 'CPU run used a GPU')
        samples[name] = sample
        if tracing:
            traces[name] = trace_proof(case, ranks, 23*math.prod(shape), max(1,ranks//2) if variant == 'before' else ranks)
        else:
            water.require(not list(case.glob('scalapack-*.jsonl')), 'Traced timing case')
    comparisons = {name: water.fixtures.compare_science(sample, samples[name[:-5]+'before'])
                   for name,sample in samples.items() if name.endswith('after')}
    medians = {variant: statistics.median(samples[f'pair-{i}-{variant}']['cp2k_seconds'] for i in (1,2,3)) for variant in ('before','after')}
    solver = {variant: statistics.median(samples[f'pair-{i}-{variant}']['timers']['cp_fm_syevd']['seconds'] for i in (1,2,3)) for variant in ('before','after')}
    hashes = {str(p.relative_to(root)): water.digest(p) for p in (root/'cases').rglob('*') if p.is_file()}
    result = dict(accepted=scheduler, scheduler_pending=not scheduler, job=job, request_sha256=water.digest(root/'request.json'),
                  mpi=traces, comparisons=comparisons, median_seconds=medians, syevd_median_seconds=solver,
                  before_over_after=medians['before']/medians['after'], measured_pairs=3, excluded_warmup_pairs=1,
                  timing_layout=dict(ranks=4*request['gpus'], threads=1), shape=request['shape'], files=hashes)
    water.write_json(root/'results'/('verified.json' if scheduler else 'completed-pending-accounting.json'), result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('files','comparisons','mpi')}), flush=True)
    return result


def submit(args):
    water.require(args.gpus in water.POINTS[args.partition], 'Unsupported resource point')
    water.require(re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,80}',args.name), 'Unsafe name')
    build = Path(args.build)
    water.require((build/'results/build-complete').is_file(), 'Repair build incomplete')
    proof = json.loads((build/'results/relink-proof.json').read_text())
    water.require(proof['control_byte_identical'] and proof['public_module_byte_identical'], 'Nonminimal build change')
    root = water.ROOT/'runtime-tests'/('cp2k-mpi-retest-'+args.name)
    root.mkdir()
    for folder in ('results','cases','fixtures','control','runtime'):
        (root/folder).mkdir()
    for folder in ('controller','deployment/cp2k-water-scaling'):
        shutil.copytree(water.REPO/folder, root/'control'/folder, ignore=shutil.ignore_patterns('__pycache__'))
    prefix = Path('/opt/apps/cp2k')/water.BUILD/args.partition
    for name, checksum in water.fixtures.DATA_HASHES.items():
        source = prefix/'share/cp2k/data'/name
        water.require(water.digest(source) == checksum, 'Changed basis/potential')
        shutil.copyfile(source, root/'fixtures'/name)
    trace_shape = [6,6,6] if args.gpus == 16 else [4,4,4]
    water.require(((23*math.prod(trace_shape)+239)//240)*4 >= 4*args.gpus, 'Probe would legitimately redistribute')
    request = dict(build=str(build), partition=args.partition, gpus=args.gpus, physical_cores=4*args.gpus,
                   stage='mpi-retest', shape=args.shape, trace_shape=trace_shape, case_seconds=7200, host_memory_fraction=.95,
                   tracer_sha256=water.digest(build/'install/lib64/trace-scalapack.so'))
    request['binaries'] = {k:dict(executable=water.digest(p), library=water.cpu_library_digest(p)) for k,p in binaries(request).items()}
    request['inputs'] = {str(p.relative_to(root)):water.digest(p) for f in ('control','fixtures') for p in (root/f).rglob('*') if p.is_file()}
    water.write_json(root/'request.json', request)
    qos = ('rush-1o2gpu' if args.partition == '4V100' else 'flood-1o2gpu') if args.gpus == 1 else ('huge-gpu' if args.gpus == 16 else 'rush-gpu')
    argv = ['sbatch','--parsable','--partition='+args.partition,'--qos='+qos,'--nodes=1','--ntasks='+str(4*args.gpus),
            '--gpus-per-node='+str(args.gpus),'--time='+str(args.minutes),'--job-name=cp2k-mpi-retest-'+args.name,
            '--output='+str(root/'results/slurm-%j.log'),str(root/'control/deployment/cp2k-water-scaling/retest-mpi.sbatch'),str(root)]
    job = subprocess.check_output(argv,text=True).strip()
    water.require(job.isdigit(),'Unexpected scheduler response')
    (root/'job.id').write_text(job+'\n')
    print(json.dumps(dict(job=job,run=str(root),partition=args.partition,gpus=args.gpus)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('submit')
    p.add_argument('--name',required=True); p.add_argument('--partition',choices=water.POINTS,required=True)
    p.add_argument('--gpus',type=int,default=4); p.add_argument('--build',required=True)
    p.add_argument('--shape',nargs=3,type=int,default=[6,6,6]); p.add_argument('--minutes',type=int,default=240)
    p = sub.add_parser('execute'); p.add_argument('root',type=Path)
    p = sub.add_parser('verify'); p.add_argument('root',type=Path)
    args = parser.parse_args()
    if args.command == 'submit': submit(args)
    elif args.command == 'execute': execute(args.root)
    else: verify(args.root)
