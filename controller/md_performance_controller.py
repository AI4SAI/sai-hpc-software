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
from md_acceptance_recovery import validate as validate_recovery

RESOURCES = dict(nodes=1, ranks=1, gpus_per_node=1, threads_per_rank=1)
BASELINE = '/opt/apps/lammps/lammps-4Jul2026-deepmd3.2.0-plumed2.10.1-nvhpc263-ompi5010-sm70/bin/lmp'


def task(run_id):
    path = ROOT / 'runtime-tests' / safe_name(run_id)
    if path.resolve() != path:
        raise ValueError('untrusted performance task path')
    return path


def validate_request(request):
    if request['run_id'] not in (request['build_run'] + '-performance', request['build_run'] + '-performance-retest'):
        raise ValueError('MD performance permits only the first attempt and one named repeat per build')
    delivery = validate_pair(request['delivery'])
    recovery = request.get('controller_recovery')
    if recovery is not None:
        validate_recovery(recovery, ROOT, CONTROL, request['build_run'], delivery)
    if (request['recipe_sha256'] != fingerprint(CONTROL)
            or (recovery is None and delivery['recipe_sha256'] != request['recipe_sha256'])
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
             'export HOME=' + shlex.quote(str(Path.home())),
             'export USER=stardust LOGNAME=stardust',
             'export SAI_SOFTWARE_ROOT=' + shlex.quote(str(PROJECT)),
             'export SAI_MD_DELIVERY=' + shlex.quote(json.dumps(delivery, sort_keys=True)),
             'export SAI_MD_IMAGE=' + shlex.quote(request['artifact']),
             join(['bash', CONTROL / 'md_performance.sh', request['run_id'], request['science_run'],
                   *([request['repeat_of']['run_id']] if request.get('repeat_of') else [])])]
    if request.get('repeat_of'):
        lines.insert(2, '#SBATCH --nodelist=' + safe_name(request['repeat_of']['node']))
    return '\n'.join(lines) + '\n'


def science_evidence(request):
    """Revalidate the source-bound scientific oracle before trusting performance."""
    evidence = science.verify(request['science_run'])
    original = json.loads((science.task(request['science_run']) / 'request.json').read_text())
    if (evidence.get('passed') is not True or original.get('build_run') != request['build_run']
            or original.get('controller_recovery') != request.get('controller_recovery')
            or evidence.get('controller_recovery') != request.get('controller_recovery')
            or original.get('nodes') != 2 or original.get('ranks') != 4
            or any(evidence.get(key) != request[key] for key in ('artifact', 'artifact_sha256', 'delivery'))):
        raise ValueError('performance requires matching two-node scientific acceptance')
    return evidence


def regular(path):
    path = Path(path)
    if path.resolve() != path or path.is_symlink() or not path.is_file():
        raise ValueError('untrusted performance evidence file')
    return path


def completed_original(run_id):
    directory = task(run_id)
    request = json.loads(regular(directory / 'request.json').read_text())
    if request.get('run_id') != run_id or request.get('repeat_of') or run_id.endswith('-retest'):
        raise ValueError('MD retest requires the first original performance attempt')
    status = json.loads(regular(directory / 'results/status.json').read_text())
    evidence_file = regular(directory / 'results/evidence.json')
    evidence = verify(run_id, enforce_performance=False)
    if (status.get('state') != 'COMPLETED' or status.get('exit_code') != '0:0'
            or status.get('job') != evidence['job'] or status.get('performance_verified') is not False
            or status.get('evidence_sha256') != checksum(evidence_file)
            or json.loads(evidence_file.read_text()) != evidence
            or evidence.get('passed') is not False or not evidence.get('regressed_backends')):
        raise ValueError('MD retest requires unchanged original raw evidence of a greater-than-10% regression')
    return request, evidence


def repeat_binding(run_id):
    directory = task(run_id)
    frozen = {backend: json.loads(regular(directory / 'results' / backend / 'frozen.json').read_text())
              for backend in BACKENDS}
    nodes = {safe_name(value['node']) for value in frozen.values()}
    if len(nodes) != 1 or any(value.get('repeat_of') for value in frozen.values()):
        raise ValueError('original MD calibration must belong to one node and not be a repeat')
    return dict(run_id=run_id, node=nodes.pop(), request_sha256=checksum(regular(directory / 'request.json')),
                evidence_sha256=checksum(regular(directory / 'results/evidence.json')),
                frozen_sha256={backend: checksum(regular(directory / 'results' / backend / 'frozen.json'))
                               for backend in BACKENDS})


def check_repeat_request(request, original):
    fields = ('build_run', 'science_run', 'delivery', 'artifact', 'artifact_sha256',
              'target', 'resources', 'recipe_sha256', 'controller_recovery')
    if any(request.get(key) != original.get(key) for key in fields):
        raise ValueError('MD retest changed its image, scientific inputs, source or allocation')


def original_for_repeat(request):
    run_id = request['run_id']
    repeat = request.get('repeat_of', {})
    original_id = run_id.removesuffix('-retest')
    if (not run_id.endswith('-retest') or original_id.endswith('-retest')
            or repeat.get('run_id') != original_id):
        raise ValueError('MD retest is not bound to its first original attempt')
    original, evidence = completed_original(original_id)
    check_repeat_request(request, original)
    if repeat != repeat_binding(original_id):
        raise ValueError('MD original regression or calibrated workload changed after repeat submission')
    return evidence


def verify_repeat(request, reports):
    if not request['run_id'].endswith('-retest') and not request.get('repeat_of'):
        for backend in BACKENDS:
            frozen = json.loads(regular(task(request['run_id']) / 'results' / backend / 'frozen.json').read_text())
            if frozen.get('repeat_of'):
                raise ValueError('first MD attempt cannot borrow a different calibrated workload')
        return None
    original = original_for_repeat(request)
    keys = ('schema', 'workload', 'backend', 'atoms', 'steps', 'node', 'resources',
            'fixture_sha256', 'input_sha256', 'input', 'minimum_engine_seconds')
    for backend in BACKENDS:
        report, previous = reports[backend], original['reports'][backend]
        if (report.get('binaries') != previous.get('binaries') or not report.get('binaries')
                or report['fixture_sha256'] != previous['fixture_sha256']
                or report['performance']['device'] != previous['performance']['device']):
            raise ValueError('MD retest changed baseline/candidate binaries, scientific inputs or device')
        before = json.loads(regular(task(request['repeat_of']['run_id']) / 'results' / backend / 'frozen.json').read_text())
        after = json.loads(regular(task(request['run_id']) / 'results' / backend / 'frozen.json').read_text())
        if (any(after.get(key) != before.get(key) for key in keys)
                or after.get('repeat_of') != {'frozen_sha256': request['repeat_of']['frozen_sha256'][backend]}):
            raise ValueError('MD retest changed the original frozen calibrated workload')
    return request['repeat_of']


def submit(args):
    scientific = science.task(args.science_run)
    original = json.loads((scientific / 'request.json').read_text())
    if original['build_run'] != args.build_run:
        raise ValueError('performance science handoff belongs to another build')
    request = dict(run_id=safe_name(args.run_id), build_run=safe_name(args.build_run),
                   science_run=safe_name(args.science_run), delivery=original['delivery'],
                   artifact=original['artifact'], artifact_sha256=original['artifact_sha256'],
                   target=original['target'], resources=RESOURCES, recipe_sha256=fingerprint(CONTROL))
    if 'controller_recovery' in original:
        request['controller_recovery'] = original['controller_recovery']
    validate_request(request)
    science_evidence(request)
    if args.run_id.endswith('-retest'):
        original_id = args.run_id.removesuffix('-retest')
        previous, _ = completed_original(original_id)
        check_repeat_request(request, previous)
        request['repeat_of'] = repeat_binding(original_id)
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
    binaries = [report.get('binaries') for report in reports.values()]
    if not binaries[0] or any(value != binaries[0] for value in binaries[1:]):
        raise ValueError('MD performance backends measured different baseline/candidate binaries')
    repeat = verify_repeat(request, reports)
    evidence = dict(passed=not regression, performance_verified=not regression, published=False,
                    build_run=request['build_run'], science_run=request['science_run'],
                    artifact=request['artifact'], artifact_sha256=request['artifact_sha256'], delivery=delivery,
                    recipe_sha256=request['recipe_sha256'], job=job, reports=reports, files=files,
                    regressed_backends=regression)
    if 'controller_recovery' in request:
        evidence['controller_recovery'] = request['controller_recovery']
    if repeat is not None:
        evidence['repeat_of'] = repeat
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
