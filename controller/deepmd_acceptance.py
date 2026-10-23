#!/usr/bin/env python3
"""Continue independent DeePMD science from an immutable verified SIF.

The original build allocation/controller/recipe remain unchanged. A new Slurm
allocation records this acceptance controller separately from build provenance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess

from deepmd_standalone import CONTROL, PROJECT, ROOT, fingerprint, task
from remote_controller import container_command, gpumd_qos, private_scratch, project_batch_directives, site_app_binds
from source_cache import checksum


def acceptance_fingerprint():
    return hashlib.sha256((fingerprint() + '\n' + checksum(CONTROL / 'deepmd_acceptance.py')).encode()).hexdigest()


def origin(source_run):
    directory = task(source_run)
    request = json.loads((directory / 'request.json').read_text())
    control = Path(request['controller'])
    if control.resolve() != control or not control.is_relative_to(ROOT / 'controller'):
        raise ValueError('original build controller escaped its frozen snapshot')
    validate = '''import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from deepmd_standalone import CONTROL, fingerprint, render, validate_delivery
from source_cache import checksum
directory = Path(sys.argv[2])
request = json.loads((directory / 'request.json').read_text())
validate_delivery(request['delivery'])
if (request['controller'] != str(CONTROL) or request['controller_sha256'] != fingerprint()
    or request['job_script_sha256'] != checksum(directory / 'job.sbatch')
    or (directory / 'job.sbatch').read_text() != render(request['delivery'], directory.name)):
    raise ValueError('original frozen build provenance changed')
'''
    subprocess.run(['python3', '-c', validate, str(control), str(directory)], check=True,
                   env=dict(os.environ, SAI_SOFTWARE_ROOT=str(PROJECT), PYTHONDONTWRITEBYTECODE='1'))
    accounting = subprocess.check_output(['sacct', '-X', '-j', request['job_id'], '--noheader', '--parsable2',
        '--format=JobIDRaw,State,ExitCode,Elapsed,NodeList,Account,WorkDir'], text=True).strip()
    rows = [line.split('|') for line in accounting.splitlines() if line.split('|')[0] == request['job_id']]
    if len(rows) != 1 or rows[0][1].split('+')[0] not in ('COMPLETED', 'FAILED', 'CANCELLED', 'TIMEOUT'):
        raise ValueError('independent acceptance requires a terminated original allocation')
    if rows[0][5:7] != ['proj-sai-hpc-software', str(directory)]:
        raise ValueError('original build used a different project account or work directory')
    log = directory / 'results' / ('slurm-' + request['job_id'] + '.log')
    if 'DEEPMD_STANDALONE_BUILD_VERIFIED' not in log.read_text(errors='replace'):
        raise ValueError('original final SIF never passed the build verification gate')
    image = directory / 'candidate.sif'
    if image.resolve() != image or not image.is_file() or image.stat().st_mode & 0o222:
        raise ValueError('original candidate must be a regular immutable SIF')
    return dict(source_run=source_run, build_request=request, build_request_sha256=checksum(directory / 'request.json'),
                build_log_sha256=checksum(log), build_accounting=accounting,
                artifact=str(image), artifact_sha256=checksum(image))


def render(run_id, source):
    directory = task(run_id)
    identity = source['build_request']['delivery']['identity']
    image = Path(source['artifact'])
    payload = json.dumps(source['build_request']['delivery'], sort_keys=True, separators=(',', ':'))
    shell = ('source /runtime/runtime-env.sh; export SAI_DEEPMD_DELIVERY="$2"; '
             'export OMP_NUM_THREADS=1 DP_INTRA_OP_PARALLELISM_THREADS=1 DP_INTER_OP_PARALLELISM_THREADS=1; '
             'exec "$1/bin/python" /acceptance/deepmd_standalone_check.py science')
    argv = container_command(image, ['/usr/bin/bash', '-euo', 'pipefail', '-c', shell, 'bash', identity['install_prefix'], payload],
        control=Path(source['build_request']['controller']), jobs=1, gpu=True, runtime=directory / 'runtime',
        extra_binds=[*site_app_binds('deepmd-kit'), (CONTROL, '/acceptance')])
    argv = [arg for arg in argv if arg != '--fakeroot']
    position = argv.index(str(image))
    argv[position:position] = ['--bind', str(directory / 'runtime') + ':/tmp:rw']
    lines = ['#!/usr/bin/env bash', '#SBATCH --job-name=deepmd-science-' + run_id,
             '#SBATCH --partition=' + identity['partition'], '#SBATCH --qos=' + gpumd_qos(PROJECT, identity['target']),
             '#SBATCH --nodes=1', '#SBATCH --ntasks=1', '#SBATCH --gpus-per-node=2', '#SBATCH --time=30',
             *project_batch_directives(PROJECT, directory), '#SBATCH --output=' + str(directory / 'results/slurm-%j.log'),
             '#SBATCH --export=NIL', 'set -eo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""',
             'source /etc/profile.d/lmod.sh', 'module load apptainer/1.4.4', 'set -u', 'umask 077',
             'export TMPDIR=' + shlex.quote(str(directory / 'runtime')), 'export APPTAINER_TMPDIR="$TMPDIR"',
             'export APPTAINER_CACHEDIR=' + shlex.quote(str(directory / 'apptainer-cache')),
             'export APPTAINERENV_SAI_MD_ALLOCATED_JOB="$SLURM_JOB_ID"',
             'export APPTAINERENV_SAI_MD_ALLOCATED_NODE="$(hostname)"',
             'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
             shlex.join(argv), 'echo DEEPMD_INDEPENDENT_ACCEPTANCE_VERIFIED_NOT_PUBLISHED']
    return '\n'.join(lines) + '\n'


def submit(run_id, source_run):
    source = origin(source_run)
    directory = task(run_id)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / 'results').mkdir()
    for name in ('runtime', 'apptainer-cache'):
        private_scratch(directory / name)
    for name in ('runtime-env.sh', 'baseline-inventory.json'):
        shutil.copy2(task(source_run) / 'runtime' / name, directory / 'runtime' / name)
    shutil.copytree(task(source_run) / 'runtime/case', directory / 'runtime/case')
    script = directory / 'job.sbatch'
    script.write_text(render(run_id, source))
    subprocess.run(['sbatch', '--test-only', str(script)], check=True, capture_output=True, cwd=directory)
    job = subprocess.check_output(['sbatch', '--hold', '--parsable', str(script)], text=True, cwd=directory).strip().split(';')[0]
    if not job.isdigit():
        raise ValueError('invalid acceptance allocation')
    request = dict(schema=1, source=source, job_id=job, controller=str(CONTROL),
                   controller_sha256=acceptance_fingerprint(), job_script_sha256=checksum(script))
    (directory / 'request.json').write_text(json.dumps(request, sort_keys=True, indent=2) + '\n')
    (directory / 'request.json').chmod(0o444)
    script.chmod(0o444)
    subprocess.run(['scontrol', 'release', job], check=True)
    print(json.dumps(request, sort_keys=True))


def status(run_id):
    from deepmd_standalone_check import verify_science_files
    directory = task(run_id)
    request = json.loads((directory / 'request.json').read_text())
    if (request['controller'] != str(CONTROL) or request['controller_sha256'] != acceptance_fingerprint()
            or request['job_script_sha256'] != checksum(directory / 'job.sbatch')
            or (directory / 'job.sbatch').read_text() != render(run_id, request['source'])
            or origin(request['source']['source_run']) != request['source']):
        raise ValueError('independent acceptance or original build provenance changed')
    accounting = subprocess.check_output(['sacct', '-X', '-j', request['job_id'], '--noheader', '--parsable2',
        '--format=JobIDRaw,State,ExitCode,Elapsed,NodeList,Account,WorkDir'], text=True).strip()
    rows = [line.split('|') for line in accounting.splitlines() if line.split('|')[0] == request['job_id']]
    if len(rows) != 1 or rows[0][5:7] != ['proj-sai-hpc-software', str(directory)]:
        raise ValueError('acceptance allocation is not in its project account/work directory')
    result = dict(schema=1, source=request['source'], job_id=request['job_id'], accounting=accounting,
                  scientific_verified=False, native_runtime_verified=False, published=False)
    if rows[0][1:3] == ['COMPLETED', '0:0']:
        result['science'] = verify_science_files(directory / 'runtime')
        result['scientific_verified'] = True
    (directory / 'results/status.json').write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print(json.dumps(result, sort_keys=True))
    return 0 if result['scientific_verified'] else 3 if rows[0][1] in ('RUNNING', 'PENDING', 'COMPLETING') else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    start = sub.add_parser('submit')
    start.add_argument('run_id')
    start.add_argument('--source-run', required=True)
    query = sub.add_parser('status')
    query.add_argument('run_id')
    args = parser.parse_args()
    if args.command == 'submit':
        submit(args.run_id, args.source_run)
        return 0
    return status(args.run_id)


if __name__ == '__main__':
    raise SystemExit(main())
