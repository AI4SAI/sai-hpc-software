#!/usr/bin/env python3
"""Image-bound scientific acceptance. Performance/publication remain separate."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
from md_controller import PROJECT, ROOT, CONTROL, task as build_task, join
from md_science import verify_science, parse_reference, parse_lammps_output, verify_plumed_output, _load_fixture, TOLERANCES
from md_tracking import TARGETS as MD_TARGETS, fingerprint
from remote_controller import TARGETS, safe_name
from source_cache import checksum


def task(run_id):
    path = ROOT / 'runtime-tests' / safe_name(run_id)
    if path.resolve() != path:
        raise ValueError('untrusted acceptance task path')
    return path


def render(request, directory):
    target = TARGETS[request['target']]
    nodes = request['nodes']
    if nodes not in (1, 2) or request['ranks'] != nodes * 2:
        raise ValueError('acceptance requires two host-MPI ranks per node')
    lines = ['#!/usr/bin/env bash', '#SBATCH --export=NIL', '#SBATCH --job-name=md-science-' + request['run_id'],
             '#SBATCH --partition=' + target['partition'], '#SBATCH --qos=' + target['qos'],
             f'#SBATCH --nodes={nodes}', '#SBATCH --ntasks-per-node=2', '#SBATCH --gpus-per-node=1',
             '#SBATCH --time=30', f'#SBATCH --output={directory}/results/slurm-%j.log',
             'set -eo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""',
             'export USER=stardust LOGNAME=stardust',
             'export SAI_SOFTWARE_ROOT=' + shlex.quote(str(PROJECT)),
             'export SAI_MD_VERSION=' + shlex.quote(request['version']),
             'export SAI_MD_IMAGE=' + shlex.quote(request['artifact']),
             'export SAI_MD_ACCEPTANCE_RANKS=' + str(request['ranks']),
             join(['bash', CONTROL / 'md_acceptance.sh', request['run_id']])]
    return '\n'.join(lines) + '\n'


def submit(args):
    build = build_task(args.build_run)
    source_request = json.loads((build / 'request.json').read_text())
    status = json.loads((build / 'results/status.json').read_text())
    artifact = Path(status['artifact'])
    if (status.get('build_verified') is not True or status.get('state') != 'COMPLETED'
            or status.get('exit_code') != '0:0' or artifact.is_symlink()
            or artifact.resolve() != artifact or checksum(artifact) != status.get('artifact_sha256')):
        raise ValueError('not a verified immutable candidate')
    if source_request['target'] not in MD_TARGETS:
        raise ValueError('unregistered acceptance target')
    r = task(args.run_id)
    if (r / 'job.id').exists():
        raise ValueError('acceptance was already submitted')
    for part in ('case', 'results', 'runtime'):
        (r / part).mkdir(parents=True, exist_ok=True)
    request = dict(run_id=args.run_id, build_run=args.build_run, version=source_request['version'],
                   target=source_request['target'], sources=source_request['sources'],
                   artifact=str(artifact), artifact_sha256=checksum(artifact), nodes=args.nodes, ranks=args.nodes * 2,
                   acceptance_recipe_sha256=fingerprint(CONTROL))
    script = r / 'job.sbatch'
    script.write_text(render(request, r))
    subprocess.run(['bash', '-n', str(script)], check=True)
    request['job_script_sha256'] = checksum(script)
    (r / 'request.json').write_text(json.dumps(request, sort_keys=True) + '\n')
    job = subprocess.check_output(['sbatch', '--parsable', str(script)], text=True).strip().split(';')[0]
    if not job.isdigit():
        raise ValueError('invalid job handle')
    (r / 'job.id').write_text(job + '\n')
    print(job, flush=True)


def verify(run_id):
    r = task(run_id)
    request = json.loads((r / 'request.json').read_text())
    if (request['acceptance_recipe_sha256'] != fingerprint(CONTROL)
            or request['job_script_sha256'] != checksum(r / 'job.sbatch')
            or checksum(request['artifact']) != request['artifact_sha256']):
        raise ValueError('acceptance image/script/verifier changed')
    records = []
    files = {}
    job = (r / 'job.id').read_text().strip()
    if not job.isdigit():
        raise ValueError('invalid acceptance job handle')
    source_sha = request['sources']['deepmd-kit']['sha']
    oracle = subprocess.check_output(['git', '--git-dir', str(ROOT / 'cache/repositories/deepmd-kit'),
                                     'show', source_sha + ':source/lmp/tests/test_lammps.py'], text=True)
    graph = subprocess.check_output(['git', '--git-dir', str(ROOT / 'cache/repositories/deepmd-kit'),
                                    'show', source_sha + ':source/tests/infer/deeppot.pbtxt'], text=True)
    reference = parse_reference(oracle)
    fixtures = {backend: _load_fixture(r / 'case', backend) for backend in ('tf', 'pt', 'jax')}
    if any(any(f.get(key) != reference[key] for key in ('reference', 'coordinates', 'atom_types', 'box'))
           or f.get('tolerances') != TOLERANCES
           or f.get('source_reference_sha256') != hashlib.sha256(oracle.encode()).hexdigest()
           or f.get('source_graph_sha256') != hashlib.sha256(graph.encode()).hexdigest()
           for f in fixtures.values()):
        raise ValueError('packaged scientific oracle differs from requested upstream commit')
    for side in ('baseline', 'candidate'):
        for backend in ('tf', 'pt', 'jax'):
            for engine in ('python', 'lammps'):
                path = r / 'results' / f'{side}-{backend}-{engine}.json'
                if path.is_symlink() or path.resolve() != path:
                    raise ValueError('untrusted scientific result path')
                rows = json.loads(path.read_text())
                if not rows or any(row.get('implementation') != side or row.get('backend') != backend
                                   or row.get('engine') != engine or row.get('slurm_job') != job for row in rows):
                    raise ValueError('mislabelled scientific results')
                expected_resources = dict(nodes=request['nodes'], ranks=request['ranks'], gpus_per_node=1, omp_threads=1)
                if any(row.get('resources') != expected_resources or row.get('reference') != reference['reference']
                       or row.get('tolerances') != TOLERANCES
                       or row.get('input_sha256') != fixtures[backend]['models'][backend]['input_sha256'] for row in rows):
                    raise ValueError('scientific workload or resource provenance differs')
                if engine == 'lammps':
                    for index, row in enumerate(rows):
                        trial = r / 'results' / f'{side}-{backend}-trials' / str(index)
                        raw = {}
                        for name in ('stdout.txt', 'result.dump', 'COLVAR'):
                            entry = trial / name
                            if entry.is_symlink() or entry.resolve() != entry:
                                raise ValueError('untrusted raw scientific result')
                            raw[name] = entry.read_text()
                            files[str(entry.relative_to(r))] = checksum(entry)
                        observed = parse_lammps_output(raw['stdout.txt'], raw['result.dump'])
                        if observed != row['observables']:
                            raise ValueError('raw LAMMPS output differs from JSON report')
                        verify_plumed_output(raw['COLVAR'], reference['distance_angstrom'])
                        hosts = []
                        for rank in range(request['ranks']):
                            trace = trial / 'sai-ranks' / f'rank-{rank}.tsv'
                            if trace.is_symlink() or trace.resolve() != trace:
                                raise ValueError('untrusted rank trace')
                            fields = trace.read_text().strip().split('\t')
                            expected_executable = ('/opt/apps/lammps/lammps-4Jul2026-deepmd3.2.0-plumed2.10.1-nvhpc263-ompi5010-sm70/bin/lmp'
                                                   if side == 'baseline' else
                                                   f'/opt/software/lammps/{request["version"]}/{request["target"]}/bin/lmp')
                            if (len(fields) != 8 or fields[1:3] != [str(rank), str(request['ranks'])]
                                    or fields[3:6] != [request['artifact'], side, request['target']]
                                    or fields[6] != expected_executable
                                    or not fields[7].endswith('-' + TARGETS[request['target']]['dependency_isa'])):
                                raise ValueError('MPI rank did not use the intended image, target or dependencies')
                            hosts.append(fields[0])
                            files[str(trace.relative_to(r))] = checksum(trace)
                        if len(set(hosts)) != request['nodes'] or any(hosts.count(h) != 2 for h in set(hosts)):
                            raise ValueError('incorrect native MPI rank distribution')
                records += rows
                files[str(path.relative_to(r))] = checksum(path)
    summary = verify_science(records)
    return dict(summary, artifact=request['artifact'], artifact_sha256=request['artifact_sha256'],
                job=job, files=files,
                recipe_sha256=request['acceptance_recipe_sha256'], performance_verified=False, published=False)


def monitor(args):
    r = task(args.run_id)
    job = (r / 'job.id').read_text().strip()
    if not job.isdigit():
        raise ValueError('invalid acceptance handle')
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        active = subprocess.check_output(['squeue', '-h', '-j', job, '-o', '%T|%R'], text=True).strip()
        if active:
            print(job + ': ' + active, flush=True)
        else:
            rows = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', job, '-o', 'JobIDRaw,State,ExitCode'], text=True).splitlines()
            result = next((row.split('|') for row in rows if row.split('|')[0] == job), None)
            if result and result[1] not in ('RUNNING', 'PENDING', 'COMPLETING'):
                status = dict(job=job, state=result[1], exit_code=result[2], scientific_verified=False, published=False)
                (r / 'results/status.json').write_text(json.dumps(status) + '\n')
                if result[1:3] != ['COMPLETED', '0:0']:
                    return 1
                evidence = verify(args.run_id)
                (r / 'results/evidence.json').write_text(json.dumps(evidence, sort_keys=True) + '\n')
                status.update(scientific_verified=True, evidence_sha256=checksum(r / 'results/evidence.json'))
                (r / 'results/status.json').write_text(json.dumps(status, sort_keys=True) + '\n')
                return 0
        time.sleep(args.interval)
    raise TimeoutError(f'observation expired; job {job} has not been cancelled or resubmitted')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='op', required=True)
    p = sub.add_parser('submit'); p.add_argument('run_id'); p.add_argument('build_run'); p.add_argument('--nodes', type=int, default=1)
    p = sub.add_parser('monitor'); p.add_argument('run_id'); p.add_argument('--timeout', type=int, default=14400); p.add_argument('--interval', type=int, default=30)
    p = sub.add_parser('verify'); p.add_argument('run_id')
    args = parser.parse_args()
    if args.op == 'verify':
        print(json.dumps(verify(args.run_id), sort_keys=True))
    else:
        raise SystemExit(submit(args) if args.op == 'submit' else monitor(args))
