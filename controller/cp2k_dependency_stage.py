#!/usr/bin/env python3
"""Build and seal a native dependency checkpoint for one exact CP2K identity.

The application receives a checked clone. The successful dependency overlay and
its logs remain available for diagnosis; no old installation is relabelled.
"""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import time

from cp2k_spack_link import validate_receipt
from cp2k_image_driver import wrap_command, validate_receipt as validate_driver_receipt
from release_contract import validate_identity
from remote_controller import TARGETS, container_command, require_capacity, safe_name
from source_cache import checksum

SEED_SHA256 = '80497b787dc178876e71098840cf465deb63c6c9bec27c9dd5cc0bed3643c337'
PURPOSE = 'cp2k-final-prefix-dependencies-not-application-acceptance'
EVIDENCE = ('install-success.json', 'libint-export-repair.json', 'spack.yaml',
            'spack.lock', 'native-profile.json', 'validated-graph.json',
            'probe-success.json', 'stage-identity.json', 'image-driver.json')


def paths(project, controller, run):
    project, controller, run = Path(project), Path(controller), Path(run)
    for path in (project, controller, run):
        if not path.is_absolute() or path.resolve() != path:
            raise ValueError('stage paths must be canonical')
    if run.parent != project / 'runs' or not controller.is_relative_to(project / 'controller'):
        raise ValueError('stage paths escaped the project')
    safe_name(run.name)
    return project, controller, run


def identity_for_stage(identity):
    identity = validate_identity(identity)
    if identity['software'] != 'cp2k' or identity['target'] not in (
            'dsprhbm', '4v100-avx512', '16v100-avx2', '8v100v0-avx512'):
        raise ValueError('native dependency stage requires a supported CP2K identity')
    return identity


def render(project, controller, run, identity, *, minutes=240, jobs=None, overlay_mb=32768):
    project, controller, run = paths(project, controller, run)
    identity = identity_for_stage(identity)
    target = TARGETS[identity['target']]
    # 8V100V0 grants six CPUs per GPU. Its six-worker Libint build exceeded
    # the four-hour QOS, so use two GPUs to obtain twelve CPUs for dependencies.
    # Application compilation and scientific acceptance retain their own resources.
    build_gpus = 2 if identity['target'] == '8v100v0-avx512' else target['gpus']
    jobs = (12 if build_gpus == 2 else target.get('build_jobs', 8)) if jobs is None else jobs
    # Site QOS observed 2026-10-15: flood-1o2gpu 4h, rush-cpu 48h.
    maximum_minutes = 240 if target['gpus'] else 2880
    if not 1 <= jobs <= 16 or not 1 <= minutes <= maximum_minutes or not 8192 <= overlay_mb <= 32768:
        raise ValueError('dependency stage resource request outside policy bounds')
    seed = project / 'cache/spack/bootstrap/spack-source-v2.ext3'
    overlay = run / 'work.ext3'
    args = ['/usr/bin/env', 'SAI_BUILD_PARTITION=' + target['partition'], '/bin/bash',
            '/control/cp2k_container_entry.sh', 'dependencies', 'cp2k',
            identity['source_sha'], identity['source_version'], identity['target'],
            json.dumps(identity, sort_keys=True, separators=(',', ':'))]
    command = container_command(project / 'containers/base/minimal-v1.sif', args,
        overlay=overlay, control=controller, jobs=jobs, gpu=bool(target['gpus']), extra_binds=[
            (project / 'cache/spack', '/input/spack'),
            ('/var/lib/dpkg', '/var/lib/dpkg'), ('/etc/os-release', '/etc/os-release'),
            ('/etc/alternatives', '/etc/alternatives'), ('/usr/bin', '/bin')])
    command[2:2] = ['--bind', str(run / 'results') + ':/results:rw']
    command = wrap_command(project, run, controller, command)
    q = shlex.quote
    lines = ['#!/bin/bash', '#SBATCH --job-name=sai-cp2k-deps-' + identity['target'] + '-' + identity['recipe_sha256'][:12],
             '#SBATCH --dependency=singleton', '#SBATCH --partition=' + target['partition'],
             '#SBATCH --qos=' + target['qos'], '#SBATCH --nodes=1', '#SBATCH --ntasks=1',
             '#SBATCH --time=' + str(minutes), '#SBATCH --export=NIL',
             '#SBATCH --output=' + str(run / 'results/slurm-%j.log')]
    lines += (['#SBATCH --gpus-per-node=' + str(build_gpus)] if target['gpus'] else
              ['#SBATCH --cpus-per-task=' + str(jobs), '#SBATCH --mem=28G'])
    lines += ['set -eo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""',
             'source /etc/profile.d/lmod.sh', 'module load apptainer/1.4.4', 'set -u',
             'umask 077', 'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
             'export TMPDIR=' + q(str(run / 'runtime')), 'export APPTAINER_TMPDIR="$TMPDIR"',
             'export APPTAINER_CACHEDIR=' + q(str(run / 'apptainer-cache')),
             shlex.join(['/usr/bin/python3', str(controller / 'build_capacity.py'), str(project),
                         str(run), str(overlay_mb), '--', '/bin/bash', '-seu', '-o', 'pipefail']) + " <<'SAI_BUILD_BODY'",
             shlex.join(['/usr/bin/python3', '-c',
                         f'import sys; sys.path.insert(0, {str(controller)!r}); '
                         f'from remote_controller import require_capacity; require_capacity({str(project)!r}, {overlay_mb})']),
             'test ! -e ' + q(str(overlay)),
             shlex.join(['cp', '--sparse=always', '--reflink=auto', '--', str(seed), str(overlay)]),
             shlex.join(['chmod', '600', str(overlay)]),
             shlex.join(['printf', '%s  %s\n', SEED_SHA256, str(overlay)]) + ' | sha256sum --check --status',
             shlex.join(['/usr/sbin/e2fsck', '-pf', str(overlay)]) + ' || test "$?" -eq 1',
             shlex.join(['/usr/sbin/resize2fs', str(overlay), str(overlay_mb) + 'M']),
             shlex.join(command), 'echo CP2K_DEPENDENCY_STAGE_COMPLETE_NOT_APPLICATION_ACCEPTANCE',
             'SAI_BUILD_BODY']
    return '\n'.join(lines) + '\n'


def read_json(path):
    path = Path(path)
    if not path.is_file() or path.resolve() != path:
        raise ValueError('missing or redirected dependency evidence: ' + str(path))
    return json.loads(path.read_text())


def accounting(run):
    job = (Path(run) / 'job.id').read_text().strip()
    if not job.isdigit():
        raise ValueError('invalid dependency stage job id')
    result = subprocess.run(['sacct', '-X', '-nP', '-j', job, '-o', 'JobIDRaw,State,ExitCode'],
                            check=True, capture_output=True, text=True)
    rows = [row.split('|')[1:3] for row in result.stdout.splitlines() if row.split('|')[0] == job]
    if len(rows) > 1:
        raise ValueError('ambiguous dependency stage accounting')
    return job, rows[0] if rows else None


def validate_completed(project, run, identity, controller, *, seal=False):
    project, controller, run = paths(project, controller, run)
    identity = identity_for_stage(identity)
    from software_controller import recipe_fingerprint
    if recipe_fingerprint('cp2k', controller) != identity['recipe_sha256']:
        raise ValueError('dependency recipe changed after identity resolution')
    request = read_json(run / 'request.json')
    if (request.get('purpose') != PURPOSE or request.get('identity') != identity or
            request.get('controller') != str(controller)):
        raise ValueError('dependency stage identity or controller mismatch')
    job, state = accounting(run)
    if job != request.get('job_id') or state != ['COMPLETED', '0:0']:
        raise ValueError('dependency stage is not a successful terminal allocation')
    results = run / 'results'
    validate_driver_receipt(project, read_json(results / 'image-driver.json'))
    if read_json(results / 'stage-identity.json') != identity:
        raise ValueError('dependency container identity differs from request')
    validate_receipt(read_json(results / 'install-success.json'), identity['partition'], identity['install_prefix'])
    graph = read_json(results / 'validated-graph.json')
    probe = read_json(results / 'probe-success.json')
    for record in (graph, probe):
        if record.get('partition') != identity['partition'] or record.get('install_prefix') != identity['install_prefix']:
            raise ValueError('dependency graph or probe prefix mismatch')
    if probe.get('mirror_only') is not True or probe.get('installed') is not False:
        raise ValueError('dependency stage did not use the offline source mirror')
    overlay = run / 'work.ext3'
    if not overlay.is_file() or overlay.resolve() != overlay:
        raise ValueError('missing or redirected dependency checkpoint')
    receipt = {'schema': 1, 'purpose': PURPOSE, 'identity': identity, 'job_id': job,
               'overlay_sha256': checksum(overlay), 'script_sha256': checksum(run / 'job.sbatch'),
               'evidence_sha256': {name: checksum(results / name) for name in EVIDENCE}}
    if receipt['script_sha256'] != request.get('script_sha256'):
        raise ValueError('dependency job script changed after submission')
    # Resolve every evidence path before hashing, including non-JSON spack.yaml.
    if any((results / name).resolve() != results / name for name in EVIDENCE):
        raise ValueError('redirected dependency evidence')
    sealed = results / 'dependency-stage.json'
    if sealed.exists():
        if read_json(sealed) != receipt:
            raise ValueError('sealed dependency checkpoint or evidence changed')
    elif seal:
        with sealed.open('x') as stream:
            json.dump(receipt, stream, sort_keys=True)
            stream.write('\n')
        sealed.chmod(0o444)
        overlay.chmod(0o400)
    else:
        raise ValueError('dependency checkpoint must be sealed by its monitor')
    return receipt


def submit(project, controller, run_id, identity, *, minutes=240, jobs=None, overlay_mb=32768):
    run = Path(project) / 'runs' / safe_name(run_id)
    project, controller, run = paths(project, controller, run)
    identity = identity_for_stage(identity)
    from software_controller import recipe_fingerprint
    if recipe_fingerprint('cp2k', controller) != identity['recipe_sha256']:
        raise ValueError('dependency recipe differs from the requested identity')
    require_capacity(project, overlay_mb)
    if run.exists() or run.is_symlink():
        raise ValueError('dependency run already exists; use a fresh run id')
    script_text = render(project, controller, run, identity, minutes=minutes, jobs=jobs, overlay_mb=overlay_mb)
    run.mkdir()
    for name in ('results', 'runtime', 'apptainer-cache'):
        (run / name).mkdir()
    script = run / 'job.sbatch'
    script.write_text(script_text)
    script.chmod(0o700)
    subprocess.run(['bash', '-n', str(script)], check=True)
    request = {'purpose': PURPOSE, 'identity': identity, 'controller': str(controller),
               'script_sha256': checksum(script), 'minutes': minutes, 'overlay_mb': overlay_mb}
    request_file = run / 'request.json'
    request_file.write_text(json.dumps(request, sort_keys=True) + '\n')
    result = subprocess.run(['sbatch', '--hold', '--parsable', str(script)], check=True, capture_output=True, text=True)
    job = result.stdout.strip().split(';')[0]
    if not job.isdigit():
        raise ValueError('invalid dependency sbatch response')
    (run / 'job.id').write_text(job + '\n')
    request['job_id'] = job
    request_file.write_text(json.dumps(request, sort_keys=True) + '\n')
    subprocess.run(['scontrol', 'release', job], check=True)
    print(job, flush=True)


def monitor(project, run_id, *, timeout=14400, interval=15):
    run = Path(project) / 'runs' / safe_name(run_id)
    request = read_json(run / 'request.json')
    deadline = time.monotonic() + timeout
    previous = None
    while time.monotonic() < deadline:
        job, state = accounting(run)
        if state != previous:
            print(job + ': ' + repr(state), flush=True)
            previous = state
        if state and state[0] not in {'PENDING', 'RUNNING', 'COMPLETING', 'CONFIGURING', 'SUSPENDED', 'REQUEUED'}:
            log = run / 'results' / ('slurm-' + job + '.log')
            if log.is_file():
                print('\n'.join(log.read_text(errors='replace').splitlines()[-100:]), flush=True)
            if state != ['COMPLETED', '0:0']:
                raise RuntimeError('CP2K dependency stage failed: ' + repr(state))
            validate_completed(project, run, request['identity'], request['controller'], seal=True)
            print('CP2K_DEPENDENCY_CHECKPOINT_SEALED ' + str(run), flush=True)
            return
        time.sleep(interval)
    raise TimeoutError('dependency monitor deadline reached; allocation was not cancelled')


def clone(project, controller, source_id, run_id, identity):
    project = Path(project)
    run, source = project / 'runs' / safe_name(run_id), project / 'runs' / safe_name(source_id)
    project, controller, run = paths(project, controller, run)
    receipt = validate_completed(project, source, identity, controller)
    target = run / 'work.ext3'
    if target.exists() or target.is_symlink() or not run.is_dir() or not (run / 'results').is_dir():
        raise ValueError('application clone requires a prepared run without an overlay')
    for name in (*EVIDENCE, 'dependency-stage.json'):
        destination = run / 'results' / name
        if destination.exists() or destination.is_symlink():
            raise ValueError('application dependency receipt already exists')
    require_capacity(project, read_json(source / 'request.json')['overlay_mb'])
    subprocess.run(['cp', '--sparse=always', '--reflink=auto', '--', str(source / 'work.ext3'), str(target)], check=True)
    if checksum(target) != receipt['overlay_sha256']:
        raise ValueError('dependency clone checksum mismatch')
    target.chmod(0o600)
    for name in EVIDENCE:
        value = (source / 'results' / name).read_bytes()
        destination = run / 'results' / name
        with destination.open('xb') as stream:
            stream.write(value)
        if checksum(destination) != receipt['evidence_sha256'][name]:
            raise ValueError('dependency receipt changed during clone')
    with (run / 'results/dependency-stage.json').open('x') as stream:
        json.dump(receipt, stream, sort_keys=True)
        stream.write('\n')
    print('CP2K_EXACT_DEPENDENCY_CHECKPOINT_CLONED', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='op', required=True)
    submit_parser = sub.add_parser('submit')
    for field in ('project', 'controller', 'run_id', 'identity'):
        submit_parser.add_argument(field)
    submit_parser.add_argument('--minutes', type=int, default=240)
    submit_parser.add_argument('--jobs', type=int)
    submit_parser.add_argument('--overlay-mb', type=int, default=32768)
    monitor_parser = sub.add_parser('monitor')
    monitor_parser.add_argument('project'); monitor_parser.add_argument('run_id')
    monitor_parser.add_argument('--timeout', type=int, default=14400)
    monitor_parser.add_argument('--interval', type=int, default=15)
    clone_parser = sub.add_parser('clone')
    for field in ('project', 'controller', 'source_id', 'run_id', 'identity'):
        clone_parser.add_argument(field)
    args = vars(parser.parse_args())
    operation = args.pop('op')
    if 'identity' in args:
        args['identity'] = json.loads(args['identity'])
    {'submit': submit, 'monitor': monitor, 'clone': clone}[operation](**args)


if __name__ == '__main__':
    main()
