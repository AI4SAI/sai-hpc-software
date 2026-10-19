#!/usr/bin/env python3
"""Compare cuSOLVER direct F/T, ELPA and PBE OT using one accepted CP2K SIF."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO/'controller'))
import cp2k_benchmark as fixtures
from delivery_layout import load_artifact
from remote_controller import safe_name

ROOT = Path('/home/stardust/sai-hpc-software')
SOURCE = '67b5da876dd6a76b8b021d5a04d1c81ba79a4c50'
MODES = ('cusolver-standard', 'cusolver-direct', 'elpa', 'ot')


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')


def input_text(mode, edge):
    require(mode in MODES and edge in (4, 6), 'Unsupported experiment')
    text = fixtures.water_input()
    coords = []
    for x in range(edge):
        for y in range(edge):
            for z in range(edge):
                for atom, dx, dy in (('O', 0, 0), ('H', .9572, 0), ('H', -.239987, .927297)):
                    coords.append(f'      {atom} {0.8+3.1*x+dx:.6f} {0.8+3.1*y+dy:.6f} {0.8+3.1*z:.6f}\n')
    text = text.replace(fixtures.WATER_COORDINATES, ''.join(coords))
    text = text.replace('ABC 12.0 12.0 12.0', 'ABC '+ ' '.join([f'{3.1*edge:.1f}']*3))
    text = text.replace('PERIODIC NONE', 'PERIODIC XYZ').replace('POISSON_SOLVER MT', 'POISSON_SOLVER PERIODIC')
    backend = 'ELPA' if mode == 'elpa' else 'CUSOLVER'
    controls = (f'  PREFERRED_DIAG_LIBRARY {backend}\n'
                f'  DIRECT_GENERALIZED_DIAGONALIZATION {"T" if mode == "cusolver-direct" else "F"}\n'
                '  ELPA_NEIGVEC_MIN 1\n  ELPA_PRINT T\n'
                '  &TIMINGS\n    THRESHOLD 0\n    TIMINGS_LEVEL 1\n  &END TIMINGS\n')
    text = text.replace('  RUN_TYPE ENERGY_FORCE\n', '  RUN_TYPE ENERGY_FORCE\n'+controls)
    if mode != 'ot':
        start = text.index('      &OT ON\n')
        end = text.index('      &END OT\n', start)+len('      &END OT\n')
        text = text[:start]+('      CHOLESKY RESTORE\n      LEVEL_SHIFT 0\n'
                            '      &DIAGONALIZATION\n        ALGORITHM STANDARD\n'
                            '      &END DIAGONALIZATION\n')+text[end:]
    return text


def parse_sample(folder, mode, edge):
    log = (folder/'cp2k.log').read_text()
    forces = (folder/'forces.xyz').read_text()
    # Reuse the independently checked science parser, extending only atom count.
    original_cases = fixtures.CASES
    fixtures.CASES = {**original_cases, 'tuning': dict(elements=['O', 'H', 'H']*(edge**3))}
    try:
        sample = fixtures.parse_science(log, forces, (folder/'elapsed.txt').read_text(), 'tuning')
    finally:
        fixtures.CASES = original_cases
    timers = {}
    pattern = r'^\s*(\w+)\s+(\d+)\s+([\d.]+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+\d+)?\s*$'
    for row in re.findall(pattern, log, re.M):
        if row[0].startswith(('cp_fm_', 'eigensolver', 'qs_', 'ot_')) or row[0] == 'CP2K':
            require(row[0] not in timers, 'Ambiguous timer: '+row[0])
            timers[row[0]] = dict(calls=int(row[1]), seconds=fixtures.number(row[6]))
    called = lambda name: timers.get(name, {}).get('calls', 0) > 0
    if mode == 'cusolver-direct':
        require(called('eigensolver_generalized') and called('cp_fm_general_cusolver'),
                'Direct generalized branch was not executed')
    else:
        require(not called('eigensolver_generalized') and not called('cp_fm_general_cusolver'),
                'Unexpected direct generalized execution')
    if mode == 'cusolver-standard':
        require(called('eigensolver') and called('cp_fm_diag_cusolver'), 'Standard cuSOLVER was not executed')
    elif mode == 'elpa':
        require(called('cp_fm_diag_elpa') or called('cp_fm_diag_elpa_base'), 'ELPA was not executed')
    elif mode == 'ot':
        require(any(name.startswith('ot_') and row['calls'] > 0 for name, row in timers.items()), 'OT was not executed')
    sample['timers'] = timers
    sample['scf_steps'] = [int(n) for n in re.findall(r'SCF run converged in\s+(\d+) steps', log)]
    return sample


def check_rank(folder, request):
    rank = json.loads((folder/'rank.json').read_text())
    require(rank['rank'] == 0 and rank['ranks'] == 1 and rank['image'] == request['artifact'], 'Wrong rank/image')
    require(rank['omp_threads'] == str(request['omp_threads']) and ',' not in rank['gpu'] and rank['gpu'],
            'Wrong threads or GPU visibility')
    topology = {}
    for line in (folder/'topology.txt').read_text().splitlines():
        if not line.startswith('#'):
            cpu, core, socket = map(int, line.split(','))
            topology[cpu] = (socket, core)
    cores = {topology[cpu] for cpu in rank['affinity']}
    require(len(cores) == request['omp_threads'], 'MPI affinity differs from physical-core request')
    return rank


def accepted_artifact(build_run):
    build = ROOT/'runs'/safe_name(build_run)
    artifact = Path((build/'artifact.path').read_text().strip())
    manifest = load_artifact(ROOT, artifact, software='cp2k')
    identity = manifest['identity']
    require(identity['source_sha'] == SOURCE and identity['track'] == 'release', 'Expected CP2K v2026.2 release')
    require(identity['target'] in ('16v100-avx2', '4v100-avx512', '8v100v0-avx512'), 'V100 experiment only')
    proof = manifest.get('cp2k_science', {})
    require(proof.get('verified') is True and proof['artifact_sha256'] == manifest['sha256'] and
            proof['identity'] == identity, 'Candidate must pass science before tuning')
    evidence = ROOT/'runtime-tests'/safe_name(proof['run_id'])/'results/evidence.json'
    require(digest(evidence) == proof['evidence_sha256'], 'Science evidence changed')
    return artifact, manifest


def submit(args):
    artifact, manifest = accepted_artifact(args.build_run)
    identity = manifest['identity']
    run_name = safe_name(args.name)
    root = ROOT/'runtime-tests'/('cp2k-direct-'+run_name)
    root.mkdir(mode=0o755)
    for name in ('results', 'control', 'fixtures', 'cases'):
        (root/name).mkdir()
    shutil.copytree(REPO/'deployment/cp2k-diagonalization', root/'control/deployment/cp2k-diagonalization',
                    ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(REPO/'controller', root/'control/controller', ignore=shutil.ignore_patterns('__pycache__'))
    for name, expected in fixtures.DATA_HASHES.items():
        data = subprocess.check_output(['git', '--git-dir='+str(ROOT/'cache/repositories/cp2k'),
                                       'show', fixtures.FIXTURE_REVISION+':data/'+name])
        require(hashlib.sha256(data).hexdigest() == expected, 'Scientific data changed')
        (root/'fixtures'/name).write_bytes(data)
    for mode in MODES:
        (root/'fixtures'/(mode+'.inp')).write_text(input_text(mode, args.edge))
    request = dict(build_run=args.build_run, artifact=str(artifact), artifact_sha256=manifest['sha256'],
                   identity=identity, science=manifest['cp2k_science'], edge=args.edge, waters=args.edge**3,
                   repeats=args.repeats, omp_threads=3 if identity['partition']=='8V100V0' else 4,
                   ranks=1, nodes=1, gpus=1)
    request['inputs'] = {str(p.relative_to(root)): digest(p) for tree in ('control','fixtures')
                         for p in (root/tree).rglob('*') if p.is_file()}
    write_json(root/'request.json', request)
    job = subprocess.check_output(['sbatch', '--parsable', '--partition='+identity['partition'],
        '--qos=flood-1o2gpu', '--gpus-per-node=1', '--job-name=cp2k-direct-'+run_name,
        '--output='+str(root/'results/slurm-%j.log'),
        str(root/'control/deployment/cp2k-diagonalization/job.sbatch'), str(root)], text=True).strip()
    require(job.isdigit(), 'Unexpected sbatch response')
    (root/'job.id').write_text(job+'\n')
    print(json.dumps(dict(job=job, run=str(root), request=request['identity']), sort_keys=True))


def execute(root):
    request = json.loads((root/'request.json').read_text())
    for name, expected in request['inputs'].items():
        require(digest(root/name) == expected, 'Frozen experiment changed: '+name)
    artifact, manifest = accepted_artifact(request['build_run'])
    require(str(artifact) == request['artifact'] and manifest['sha256'] == request['artifact_sha256'] and
            manifest['identity'] == request['identity'], 'Experiment artifact changed')
    require(os.environ['SLURM_JOB_PARTITION'] == request['identity']['partition'], 'Wrong partition')
    require(os.environ['OMP_NUM_THREADS'] == str(request['omp_threads']), 'Wrong OpenMP allocation')
    env = dict(os.environ, SAI_SOFTWARE_ROOT=str(ROOT), SAI_CP2K_IMAGE=str(artifact))
    env['SAI_TUNING_LAUNCHER'] = str(REPO/'controller/cp2k_runtime.sh')
    wrapper = Path(__file__).with_name('rank.sh')
    mpi = ['mpirun', '-np', '1', '--map-by', env['MAP_OPT'], '--report-bindings', 'bash', str(wrapper)]
    version = root/'results/version'
    version.mkdir()
    with (version/'cp2k.log').open('w') as stream:
        subprocess.run(mpi+['--version'], cwd=version, env=env, stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=120)
    require('2026.2' in (version/'cp2k.log').read_text(), 'Wrong executable version')
    samples = {}
    order = [('warmup', mode) for mode in MODES]
    for repeat in range(request['repeats']):
        modes = MODES if repeat % 2 == 0 else tuple(reversed(MODES))
        order.extend((str(repeat), mode) for mode in modes)
    write_json(root/'results/order.json', order)
    for repeat, mode in order:
        case = root/'cases'/(repeat+'-'+mode)
        case.mkdir()
        shutil.copyfile(root/'fixtures'/(mode+'.inp'), case/'input.inp')
        for name in fixtures.DATA_HASHES:
            shutil.copyfile(root/'fixtures'/name, case/name)
        print(f'CP2K_TUNING_START {repeat} {mode}', flush=True)
        with (case/'gpu.csv').open('w') as gpu:
            sampler = subprocess.Popen(['nvidia-smi', '-i', env['CUDA_VISIBLE_DEVICES'],
                '--query-gpu=timestamp,uuid,memory.used,utilization.gpu,clocks.sm,power.draw',
                '--format=csv,noheader,nounits', '-l', '1'], stdout=gpu, stderr=subprocess.STDOUT)
            try:
                start = time.monotonic()
                with (case/'cp2k.log').open('w') as stream:
                    subprocess.run(mpi+['-i', 'input.inp'], cwd=case, env=env, stdout=stream,
                                   stderr=subprocess.STDOUT, check=True, timeout=1800)
                (case/'elapsed.txt').write_text(str(time.monotonic()-start)+'\n')
            finally:
                sampler.terminate()
                sampler.wait(timeout=10)
        sample = parse_sample(case, mode, request['edge'])
        sample['rank'] = check_rank(case, request)
        memory = [float(line.split(',')[2]) for line in (case/'gpu.csv').read_text().splitlines()
                  if len(line.split(',')) == 6]
        require(memory, 'Missing GPU sampling')
        sample['sampled_gpu_memory_max_mib'] = max(memory)
        if samples:
            sample['comparison'] = fixtures.compare_science(sample, samples['warmup-cusolver-standard'])
        write_json(case/'sample.json', sample)
        samples[case.name] = sample
        print(f'CP2K_TUNING_PASS {repeat} {mode} compute={sample["cp2k_seconds"]}', flush=True)
    report = dict(request=request, samples=samples, scope='one node, one MPI rank, one V100; CP2K internal total time')
    report['median_seconds'] = {mode: statistics.median(samples[str(r)+'-'+mode]['cp2k_seconds']
                                   for r in range(request['repeats'])) for mode in MODES}
    report['standard_over_direct'] = report['median_seconds']['cusolver-standard']/report['median_seconds']['cusolver-direct']
    report['files'] = {str(p.relative_to(root)): digest(p) for p in root.rglob('*')
                       if p.is_file() and (p.name in ('cp2k.log','forces.xyz','input.inp','gpu.csv','affinity.txt',
                                                     'topology.txt','rank.json','elapsed.txt','sample.json','request.json'))}
    write_json(root/'results/evidence.json', report)
    (root/'results/passed.txt').write_text('CP2K_DIRECT_COMPARISON_PASS\n')


def validate(root):
    request = json.loads((root/'request.json').read_text())
    recorded = json.loads((root/'results/evidence.json').read_text())
    require(recorded['request'] == request, 'Experiment identity changed')
    for name, expected in {**request['inputs'], **recorded['files']}.items():
        require(digest(root/name) == expected, 'Raw evidence changed: '+name)
    job = (root/'job.id').read_text().strip()
    entries = [line.split('|') for line in (root/'results/accounting.txt').read_text().splitlines()]
    rows = [row for row in entries if row[0] == job]
    require(len(rows) == 1 and rows[0][1] == request['identity']['partition'] and rows[0][2:4] == ['COMPLETED','0:0'],
            'Experiment job did not complete successfully')
    require((root/'results/passed.txt').read_text().strip() == 'CP2K_DIRECT_COMPARISON_PASS', 'Missing completed experiment')
    order = [('warmup', mode) for mode in MODES]
    for repeat in range(request['repeats']):
        order += [(str(repeat), mode) for mode in (MODES if repeat%2==0 else tuple(reversed(MODES)))]
    require(json.loads((root/'results/order.json').read_text()) == [list(row) for row in order], 'Run order differs')
    samples = {}
    for repeat, mode in order:
        folder = root/'cases'/(repeat+'-'+mode)
        require((folder/'input.inp').read_text() == input_text(mode,request['edge']), 'Changed science input')
        for name, expected in fixtures.DATA_HASHES.items():
            require(digest(folder/name) == expected, 'Basis or pseudopotentials changed')
        sample = parse_sample(folder,mode,request['edge'])
        rank = check_rank(folder,request)
        require(rank['host'].split('.')[0] == rows[0][5], 'Unexpected compute node')
        if samples:
            sample['comparison'] = fixtures.compare_science(sample,samples['warmup-cusolver-standard'])
        samples[folder.name] = sample
    medians = {mode:statistics.median(samples[str(r)+'-'+mode]['cp2k_seconds'] for r in range(request['repeats']))
               for mode in MODES}
    require(medians == recorded['median_seconds'], 'Recorded medians differ from raw timings')
    return dict(job_id=job,partition=rows[0][1],state=rows[0][2],exit_code=rows[0][3],elapsed=rows[0][4],node=rows[0][5],
                identity=request['identity'],artifact_sha256=request['artifact_sha256'],waters=request['waters'],
                mode='one warmup and three alternating repetitions' if request['repeats']==3 else 'one warmup and five alternating repetitions',
                median_seconds=medians,standard_over_direct=medians['cusolver-standard']/medians['cusolver-direct'],
                max_energy_delta_ha=max(s.get('comparison',{}).get('energy_delta_ha',0) for s in samples.values()),
                max_force_delta_ha_bohr=max(s.get('comparison',{}).get('max_force_delta_ha_bohr',0) for s in samples.values()),
                samples=samples,scope=recorded['scope'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='op', required=True)
    command = commands.add_parser('submit')
    command.add_argument('--build-run', required=True)
    command.add_argument('--name', required=True)
    command.add_argument('--edge', type=int, choices=(4, 6), default=6)
    command.add_argument('--repeats', type=int, choices=(3, 5), default=3)
    command = commands.add_parser('execute')
    command.add_argument('run', type=Path)
    command = commands.add_parser('verify')
    command.add_argument('run', type=Path)
    args = parser.parse_args()
    if args.op == 'submit':
        submit(args)
    elif args.op == 'execute':
        execute(args.run)
    else:
        print(json.dumps(validate(args.run),indent=2,sort_keys=True))
