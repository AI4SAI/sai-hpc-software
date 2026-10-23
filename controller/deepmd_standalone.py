#!/usr/bin/env python3
"""Compile and validate DeePMD without rebuilding a companion MD engine."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess

from cp2k_image_driver import wrap_command
from release_contract import GPU_TARGETS, claim_build, make_identity, validate_identity
from remote_controller import (container_command, gpumd_qos, private_scratch,
                               project_batch_directives, safe_name, site_app_binds, SAI_PROJECT_ROOT)
from source_cache import checksum

PROJECT = Path(os.environ.get('SAI_SOFTWARE_ROOT', '/org/org-open-sai/proj-sai-hpc-software')).resolve()
ROOT = PROJECT / 'experimental/deepmd-standalone'
CONTROL = Path(__file__).resolve().parent
FILES = ('deepmd_standalone.py', 'deepmd_standalone_entry.sh', 'deepmd_standalone_check.py',
         'md_environment.sh', 'md_driver_paths.py', 'md_source_version.py', 'md_probe.py',
         'md_science.py', 'md_evidence.py', 'md_performance.py', 'md_performance_run.py',
         'md_packaging.py', 'md_relocate_audit.py', 'remote_controller.py',
         'source_cache.py', 'create_rootfs.sh', 'resolve_source.py', 'release_contract.py',
         'native_module.py', 'export_native.py', 'module_publication.py',
         'delivery_layout.py', 'cp2k_image_driver.py')


def fingerprint(control=CONTROL):
    digest = hashlib.sha256(b'sai-deepmd-independent-v1\n')
    for name in FILES:
        path = Path(control) / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('missing or redirected DeePMD controller: ' + name)
        digest.update(name.encode() + b'\0' + path.read_bytes())
    return digest.hexdigest()


def make_delivery(source, target, control=CONTROL):
    if target not in GPU_TARGETS:
        raise ValueError('DeePMD requires a validated GPU partition')
    identity = make_identity('deepmd-kit', source['track'], source['source_ref'],
                             source['source_sha'], source['source_version'], fingerprint(control), target)
    return dict(schema=1, identity=identity)


def validate_delivery(delivery, control=CONTROL):
    if not isinstance(delivery, dict) or set(delivery) != {'schema', 'identity'} or delivery['schema'] != 1:
        raise ValueError('unexpected standalone DeePMD delivery')
    identity = validate_identity(delivery['identity'])
    if identity['software'] != 'deepmd-kit' or delivery != make_delivery(identity, identity['target'], control):
        raise ValueError('standalone DeePMD source, recipe or target changed')
    return delivery


def task(run_id):
    path = ROOT / 'runs' / safe_name(run_id)
    if path.resolve() != path:
        raise ValueError('redirected DeePMD task')
    return path


def render(delivery, run_id):
    identity = validate_delivery(delivery)['identity']
    directory = task(run_id)
    base = PROJECT / 'containers/base/minimal-v1.sif'
    image, overlay = directory / 'candidate.sif', directory / 'work.ext3'
    payload = json.dumps(delivery, sort_keys=True, separators=(',', ':'))

    def command(phase):
        final = phase in ('verify', 'science')
        argv = container_command(image if final else base,
            ['/usr/bin/bash', '/control/deepmd_standalone_entry.sh', phase, payload],
            overlay=None if final else overlay, control=CONTROL, jobs=12, gpu=True,
            runtime=directory / 'runtime', extra_binds=[*site_app_binds('deepmd-kit'),
                *([] if final else [(ROOT / 'cache/repositories/deepmd-kit', '/input/deepmd-kit')])])
        if phase == 'science':
            # Nsight also writes literal /tmp paths. The backing directory is
            # this project's private scratch; physical host /tmp stays unbound.
            position = argv.index(str(image))
            argv[position:position] = ['--bind', str(directory / 'runtime') + ':/tmp:rw']
        return ([arg for arg in argv if arg != '--fakeroot'] if final
                else wrap_command(ROOT, directory, CONTROL, argv))

    emit = wrap_command(ROOT, directory, CONTROL, container_command(base,
        ['/usr/bin/cat', '/workspace/final.squashfs'], overlay=str(overlay) + ':ro', jobs=12))
    def seal(phase):
        return shlex.join(['env', 'SAI_SOFTWARE_ROOT=' + str(PROJECT), 'PYTHONDONTWRITEBYTECODE=1',
            '/usr/bin/python3', str(CONTROL / 'deepmd_standalone.py'), 'seal-' + phase, run_id])

    lines = ['#!/usr/bin/env bash', '#SBATCH --job-name=deepmd-' + safe_name(run_id),
             '#SBATCH --partition=' + identity['partition'],
             '#SBATCH --qos=' + gpumd_qos(PROJECT, identity['target']),
             '#SBATCH --nodes=1', '#SBATCH --ntasks=1', '#SBATCH --gpus-per-node=2',
             '#SBATCH --time=240', *project_batch_directives(PROJECT, directory),
             '#SBATCH --output=' + str(directory / 'results/slurm-%j.log'), '#SBATCH --export=NIL',
             'set -eo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""',
             'source /etc/profile.d/lmod.sh', 'module load apptainer/1.4.4', 'set -u', 'umask 077',
             'export TMPDIR=' + shlex.quote(str(directory / 'runtime')),
             'export APPTAINER_TMPDIR="$TMPDIR"',
             'export APPTAINER_CACHEDIR=' + shlex.quote(str(directory / 'apptainer-cache')),
             'export APPTAINERENV_SAI_MD_ALLOCATED_JOB="$SLURM_JOB_ID"',
             'export APPTAINERENV_SAI_MD_ALLOCATED_NODE="$(hostname)"',
             'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
             '[[ ${SLURM_CPUS_ON_NODE:?} -ge 12 ]]',
             shlex.join(['apptainer', 'overlay', 'create', '--fakeroot', '--sparse', '--size', '32768', str(overlay)]),
             shlex.join(command('build')), shlex.join(command('export')),
             shlex.join(emit) + ' > ' + shlex.quote(str(directory / 'final.squashfs')),
             shlex.join(['apptainer', 'sif', 'new', str(image)]),
             shlex.join(['apptainer', 'sif', 'add', '--datatype', '4', '--partfs', '1', '--parttype', '2',
                         '--partarch', '2', '--groupid', '1', str(image), str(directory / 'final.squashfs')]),
             shlex.join(command('verify')), shlex.join(['chmod', '0444', str(image)]),
             seal('build'),
             'echo DEEPMD_STANDALONE_BUILD_VERIFIED', shlex.join(command('science')),
             seal('science'),
             'echo DEEPMD_STANDALONE_SCIENCE_VERIFIED_NOT_PUBLISHED']
    return '\n'.join(lines) + '\n'


def submit(run_id, delivery, *, retry_releases=False):
    if not PROJECT.is_relative_to(SAI_PROJECT_ROOT):
        raise ValueError('new DeePMD work requires the authorized project tree')
    delivery = validate_delivery(delivery)
    directory = task(run_id)
    if directory.exists():
        raise ValueError('DeePMD task already exists; use a new run id')
    claim = claim_build(ROOT, [delivery['identity']], run_id, retry=retry_releases)
    if not claim['build']:
        print(json.dumps(claim, sort_keys=True))
        return
    (directory / 'results').mkdir()
    for name in ('runtime', 'apptainer-cache'):
        private_scratch(directory / name)
    script = directory / 'job.sbatch'
    script.write_text(render(delivery, run_id))
    record = dict(schema=1, delivery=delivery, controller=str(CONTROL),
                  controller_sha256=fingerprint(), job_script_sha256=checksum(script))
    subprocess.run(['sbatch', '--test-only', str(script)], cwd=directory, check=True, capture_output=True)
    job = subprocess.check_output(['sbatch', '--hold', '--parsable', str(script)], cwd=directory, text=True).strip().split(';')[0]
    if not job.isdigit():
        raise ValueError('invalid Slurm job id')
    record['job_id'] = job
    (directory / 'request.json').write_text(json.dumps(record, sort_keys=True, indent=2) + '\n')
    (directory / 'request.json').chmod(0o444)
    script.chmod(0o444)
    subprocess.run(['scontrol', 'release', job], check=True)
    print(json.dumps(record, sort_keys=True))


def read_request(directory):
    request = json.loads((directory / 'request.json').read_text())
    validate_delivery(request['delivery'])
    if (request['controller'] != str(CONTROL) or request['controller_sha256'] != fingerprint()
            or request['job_script_sha256'] != checksum(directory / 'job.sbatch')
            or (directory / 'job.sbatch').read_text() != render(request['delivery'], directory.name)):
        raise ValueError('DeePMD controller or submission changed')
    return request


def immutable_file(path):
    if path.resolve() != path or not path.is_file() or path.stat().st_mode & 0o222:
        raise ValueError('DeePMD completion requires a regular immutable file: ' + str(path))
    return dict(sha256=checksum(path), bytes=path.stat().st_size)


def build_receipt(directory, request):
    image = directory / 'candidate.sif'
    return dict(schema=1, kind='deepmd-build', run_id=directory.name, job_id=request['job_id'],
        delivery=request['delivery'], controller_sha256=request['controller_sha256'],
        request_sha256=checksum(directory / 'request.json'), job_script_sha256=request['job_script_sha256'],
        artifact=dict(path=str(image), **immutable_file(image)))


def verify_build_receipt(directory, request):
    path = directory / 'results/build-receipt.json'
    immutable_file(path)
    expected = build_receipt(directory, request)
    if json.loads(path.read_text()) != expected:
        raise ValueError('DeePMD artifact differs from its fixed build receipt')
    return expected


def write_receipt(path, record):
    # A rerun cannot replace the accepted hash with newly observed bytes.
    with path.open('x') as stream:
        stream.write(json.dumps(record, sort_keys=True, indent=2) + '\n')
    path.chmod(0o444)


def science_receipt(directory, request, built):
    from deepmd_standalone_check import science_evidence_hashes, verify_science_files
    runtime = directory / 'runtime'
    science = verify_science_files(runtime)
    if science != json.loads((runtime / 'science.json').read_text()):
        raise ValueError('DeePMD scientific report differs from its raw evidence')
    record = dict(schema=1, kind='deepmd-science', run_id=directory.name, job_id=request['job_id'],
        build_receipt_sha256=checksum(directory / 'results/build-receipt.json'),
        artifact_sha256=built['artifact']['sha256'],
        evidence_sha256=science_evidence_hashes(runtime, request['job_id']))
    return record, science


def verify_science_receipt(directory, request, built):
    path = directory / 'results/science-receipt.json'
    immutable_file(path)
    expected, science = science_receipt(directory, request, built)
    if json.loads(path.read_text()) != expected:
        raise ValueError('DeePMD science differs from its fixed SIF/evidence receipt')
    return science


def seal(run_id, phase):
    directory = task(run_id)
    request = read_request(directory)
    if os.environ.get('SLURM_JOB_ID') != request['job_id']:
        raise ValueError('DeePMD completion must be recorded inside its build allocation')
    if phase == 'build':
        write_receipt(directory / 'results/build-receipt.json', build_receipt(directory, request))
    elif phase == 'science':
        built = verify_build_receipt(directory, request)
        record, _ = science_receipt(directory, request, built)
        write_receipt(directory / 'results/science-receipt.json', record)
    else:
        raise ValueError('unknown DeePMD completion phase')


def status(run_id):
    directory = task(run_id)
    request = read_request(directory)
    accounting = subprocess.check_output(['sacct', '-X', '-j', request['job_id'], '--noheader', '--parsable2',
        '--format=JobIDRaw,State,ExitCode,Elapsed,NodeList,Account,WorkDir'], text=True).strip()
    if not accounting:
        # slurmdbd can lag behind sbatch/squeue, especially on the first poll.
        # This is an observation gap, never a build or acceptance claim.
        result = dict(run_id=run_id, delivery=request['delivery'], job_id=request['job_id'], accounting='',
                      state='PENDING_ACCOUNTING', build_verified=False, scientific_verified=False,
                      native_runtime_verified=False, published=False)
        (directory / 'results/status.json').write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
        print(json.dumps(result, sort_keys=True))
        return 3
    rows = [line.split('|') for line in accounting.splitlines() if line.split('|')[0] == request['job_id']]
    if len(rows) != 1:
        raise ValueError('missing or ambiguous DeePMD allocation')
    row = rows[0]
    if row[5:7] != ['proj-sai-hpc-software', str(directory)]:
        raise ValueError('DeePMD job ran outside its recorded project account or directory')
    log = directory / 'results' / ('slurm-' + request['job_id'] + '.log')
    text = log.read_text(errors='replace') if log.exists() else ''
    built = (directory / 'candidate.sif').is_file() and 'DEEPMD_STANDALONE_BUILD_VERIFIED' in text
    accepted = row[1:3] == ['COMPLETED', '0:0'] and built and 'DEEPMD_STANDALONE_SCIENCE_VERIFIED_NOT_PUBLISHED' in text
    result = dict(run_id=run_id, delivery=request['delivery'], job_id=request['job_id'], accounting=accounting,
                  build_verified=built, scientific_verified=accepted, native_runtime_verified=False, published=False)
    if built:
        receipt = verify_build_receipt(directory, request)
        result['artifact_sha256'] = receipt['artifact']['sha256']
        result['build_receipt_sha256'] = checksum(directory / 'results/build-receipt.json')
    if accepted:
        result['science'] = verify_science_receipt(directory, request, receipt)
        result['science_receipt_sha256'] = checksum(directory / 'results/science-receipt.json')
    (directory / 'results/status.json').write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print(json.dumps(result, sort_keys=True))
    active = ('PENDING', 'RUNNING', 'CONFIGURING', 'COMPLETING', 'SUSPENDED',
              'REQUEUED', 'REQUEUE_FED', 'REQUEUE_HOLD', 'RESIZING', 'SIGNALING', 'STAGE_OUT')
    return 0 if accepted else 3 if row[1].split('+')[0] in active else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    start = sub.add_parser('submit')
    start.add_argument('run_id')
    start.add_argument('--source', required=True, help='resolved release-contract source JSON')
    start.add_argument('--target', choices=GPU_TARGETS, required=True)
    start.add_argument('--retry-releases', action='store_true')
    query = sub.add_parser('status')
    query.add_argument('run_id')
    for phase in ('build', 'science'):
        completion = sub.add_parser('seal-' + phase)
        completion.add_argument('run_id')
    args = parser.parse_args()
    if args.command == 'submit':
        submit(args.run_id, make_delivery(json.loads(args.source), args.target),
               retry_releases=args.retry_releases)
        return 0
    if args.command.startswith('seal-'):
        seal(args.run_id, args.command.removeprefix('seal-'))
        return 0
    return status(args.run_id)


if __name__ == '__main__':
    raise SystemExit(main())
