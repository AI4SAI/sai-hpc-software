#!/usr/bin/env python3
"""Slurm lifecycle for raw-verified, image-bound paired MD performance acceptance."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import time

import md_acceptance_controller as science
from md_controller import ROOT, PROJECT, CONTROL, artifact_path, join, validate_pair, validate_runtime_image
from md_tracking import fingerprint
from md_science import BACKENDS, file_digest
from remote_controller import TARGETS, safe_name
from source_cache import checksum
from delivery_layout import PerformanceRegression, check_performance

RESOURCES = dict(nodes=1, ranks=1, gpus_per_node=1, threads_per_rank=1)
BASELINE = '/opt/apps/lammps/lammps-4Jul2026-deepmd3.2.0-plumed2.10.1-nvhpc263-ompi5010-sm70/bin/lmp'


def task(run_id):
    path = ROOT / 'runtime-tests' / safe_name(run_id)
    if path.resolve() != path:
        raise ValueError('untrusted performance task path')
    return path


def validate_request(request):
    delivery = validate_pair(request['delivery'])
    if (request['recipe_sha256'] != fingerprint(CONTROL)
            or delivery['recipe_sha256'] != request['recipe_sha256']
            or request['resources'] != RESOURCES
            or request['artifact'] != str(artifact_path(ROOT, delivery, request['build_run']))
            or request['target'] != delivery['plan']['target']):
        raise ValueError('performance image, recipe or allocation differs')
    validate_runtime_image(request['artifact'], delivery)
    if checksum(request['artifact']) != request['artifact_sha256']:
        raise ValueError('performance candidate changed')
    return delivery


def render(request, directory):
    delivery = validate_pair(request['delivery'])
    target = TARGETS[delivery['plan']['target']]
    if request['resources'] != RESOURCES:
        raise ValueError('performance requires one rank, one thread and one GPU')
    lines = ['#!/usr/bin/env bash', '#SBATCH --export=NIL', '#SBATCH --job-name=md-performance-' + safe_name(request['run_id']),
             '#SBATCH --partition=' + target['partition'], '#SBATCH --qos=' + target['qos'],
             '#SBATCH --nodes=1', '#SBATCH --ntasks=1', '#SBATCH --gpus-per-node=1',
             '#SBATCH --time=120', f'#SBATCH --output={directory}/results/slurm-%j.log',
             'set -eo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""',
             'export USER=stardust LOGNAME=stardust',
             'export SAI_SOFTWARE_ROOT=' + shlex.quote(str(PROJECT)),
             'export SAI_MD_DELIVERY=' + shlex.quote(json.dumps(delivery, sort_keys=True)),
             'export SAI_MD_IMAGE=' + shlex.quote(request['artifact']),
             join(['bash', CONTROL / 'md_performance.sh', request['run_id'], request['science_run']])]
    return '\n'.join(lines) + '\n'


def science_evidence(request):
    """Revalidate the source-bound scientific oracle before trusting performance."""
    evidence = science.verify(request['science_run'])
    original = json.loads((science.task(request['science_run']) / 'request.json').read_text())
    if (evidence.get('passed') is not True or original.get('build_run') != request['build_run']
            or original.get('nodes') != 2 or original.get('ranks') != 4
            or any(evidence.get(key) != request[key] for key in ('artifact', 'artifact_sha256', 'delivery'))):
        raise ValueError('performance requires matching two-node scientific acceptance')
    return evidence


def submit(args):
    scientific = science.task(args.science_run)
    original = json.loads((scientific / 'request.json').read_text())
    if original['build_run'] != args.build_run:
        raise ValueError('performance science handoff belongs to another build')
    request = dict(run_id=safe_name(args.run_id), build_run=safe_name(args.build_run),
                   science_run=safe_name(args.science_run), delivery=original['delivery'],
                   artifact=original['artifact'], artifact_sha256=original['artifact_sha256'],
                   target=original['target'], resources=RESOURCES, recipe_sha256=fingerprint(CONTROL))
    validate_request(request)
    science_evidence(request)
    directory = task(args.run_id)
    if (directory / 'job.id').exists() or (directory / 'request.json').exists():
        raise ValueError('performance was already submitted; use a new explicit repeat run')
    for name in ('case', 'runtime', 'results'):
        (directory / name).mkdir(parents=True, exist_ok=True)
    script = directory / 'job.sbatch'
    script.write_text(render(request, directory))
    subprocess.run(['bash', '-n', str(script)], check=True)
    request['job_script_sha256'] = checksum(script)
    (directory / 'request.json').write_text(json.dumps(request, sort_keys=True) + '\n')
    job = subprocess.check_output(['sbatch', '--parsable', str(script)], text=True).strip().split(';')[0]
    if not job.isdigit():
        raise ValueError('invalid performance job handle')
    (directory / 'job.id').write_text(job + '\n')
    print(job, flush=True)


def verify(run_id, *, enforce_performance=True):
    from md_performance_evidence import verify_performance_evidence
    directory = task(run_id)
    request = json.loads((directory / 'request.json').read_text())
    delivery = validate_request(request)
    if request['run_id'] != run_id or request['job_script_sha256'] != checksum(directory / 'job.sbatch'):
        raise ValueError('performance request or job script changed')
    science_evidence(request)
    job = (directory / 'job.id').read_text().strip()
    if not job.isdigit():
        raise ValueError('invalid performance job handle')
    science_case = science.task(request['science_run']) / 'case'
    case = directory / 'case'
    files = {}
    for name in ('fixture.json', 'data.lmp', 'plumed.dat', *BACKENDS.values(), *(f'in.{backend}' for backend in BACKENDS)):
        path = case / name
        if path.resolve() != path or path.is_symlink() or file_digest(path) != file_digest(science_case / name):
            raise ValueError('performance fixture differs from source-verified science inputs')
        files['case/' + name] = file_digest(path)
    nsys_file = directory / 'results/nsys.path'
    nsys = nsys_file.read_text().strip()
    if (nsys_file.resolve() != nsys_file or nsys_file.is_symlink()
            or not nsys.startswith('/opt/devtools/') or Path(nsys).resolve() != Path(nsys)
            or not Path(nsys).is_file()):
        raise ValueError('performance requires a canonical site Nsight executable')
    files['results/nsys.path'] = checksum(nsys_file)
    expected = dict(slurm_job=job, resources=RESOURCES,
                    launcher_sha256=checksum(CONTROL / 'md_runtime.sh'),
                    artifact=request['artifact'], artifact_sha256=request['artifact_sha256'],
                    target=request['target'], candidate_executable=delivery['identities']['lammps']['install_prefix'] + '/bin/lmp',
                    baseline_executable=BASELINE, nsys=nsys)
    reports = {}
    regression = []
    for backend in BACKENDS:
        report = verify_performance_evidence(directory / 'results' / backend, case, backend=backend, expected=expected)
        reports[backend] = report
        for name, digest in report['files'].items():
            files['results/' + backend + '/' + name] = digest
        performance = report['performance']
        try:
            check_performance(performance['candidate']['median_engine_seconds'], performance['baseline']['median_engine_seconds'])
        except PerformanceRegression:
            regression.append(backend)
    evidence = dict(passed=not regression, performance_verified=not regression, published=False,
                    build_run=request['build_run'], science_run=request['science_run'],
                    artifact=request['artifact'], artifact_sha256=request['artifact_sha256'], delivery=delivery,
                    recipe_sha256=request['recipe_sha256'], job=job, reports=reports, files=files,
                    regressed_backends=regression)
    if regression and enforce_performance:
        raise PerformanceRegression('MD performance exceeds 10% slowdown: ' + ', '.join(regression))
    return evidence


def monitor(args):
    directory = task(args.run_id)
    job = (directory / 'job.id').read_text().strip()
    if not job.isdigit():
        raise ValueError('invalid performance job handle')
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        active = subprocess.check_output(['squeue', '-h', '-j', job, '-o', '%T|%R'], text=True).strip()
        if active:
            print(job + ': ' + active, flush=True)
        else:
            rows = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', job, '-o', 'JobIDRaw,State,ExitCode'], text=True).splitlines()
            result = next((row.split('|') for row in rows if row.split('|')[0] == job), None)
            if result and result[1] not in ('RUNNING', 'PENDING', 'COMPLETING'):
                status = dict(job=job, state=result[1], exit_code=result[2], performance_verified=False, published=False)
                (directory / 'results/status.json').write_text(json.dumps(status, sort_keys=True) + '\n')
                if result[1:3] != ['COMPLETED', '0:0']:
                    return 1
                evidence = verify(args.run_id, enforce_performance=False)
                evidence_file = directory / 'results/evidence.json'
                evidence_file.write_text(json.dumps(evidence, sort_keys=True) + '\n')
                status.update(performance_verified=evidence['performance_verified'], evidence_sha256=checksum(evidence_file))
                (directory / 'results/status.json').write_text(json.dumps(status, sort_keys=True) + '\n')
                return 0 if evidence['performance_verified'] else 3
        time.sleep(args.interval)
    raise TimeoutError(f'observation expired; job {job} has not been cancelled or resubmitted')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='op', required=True)
    p = sub.add_parser('submit'); p.add_argument('run_id'); p.add_argument('build_run'); p.add_argument('science_run')
    p = sub.add_parser('monitor'); p.add_argument('run_id'); p.add_argument('--timeout', type=int, default=12600); p.add_argument('--interval', type=int, default=30)
    p = sub.add_parser('verify'); p.add_argument('run_id')
    args = parser.parse_args()
    try:
        if args.op == 'submit':
            return submit(args)
        if args.op == 'monitor':
            return monitor(args)
        print(json.dumps(verify(args.run_id), sort_keys=True))
    except PerformanceRegression as error:
        print(str(error), file=__import__('sys').stderr)
        return 3
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
