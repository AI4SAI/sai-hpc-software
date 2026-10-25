#!/usr/bin/env python3
"""Actions transport for independently accepted DeePMD release/development SIFs."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shlex
import subprocess
import tarfile
import time

from deepmd_standalone import CONTROL, FILES, make_delivery
from release_contract import GPU_TARGETS, resolve_tracks
from remote_controller import SAI_PROJECT_ROOT, safe_name, safe_sha
from source_cache import pack


def resolve_builds(tracks, targets, resolver=resolve_tracks):
    requested = [value.strip() for value in targets.split(',')]
    if not requested or len(set(requested)) != len(requested) or any(value not in GPU_TARGETS for value in requested):
        raise ValueError('invalid or duplicate DeePMD target')
    sources = resolver('deepmd-kit', [value.strip() for value in tracks.split(',')])
    if any(source.get('status') not in ('resolved', 'skipped') for source in sources):
        raise ValueError('unexpected DeePMD source resolution status')
    return dict(builds=[dict(source, target=target) for source in sources if source['status'] == 'resolved'
                        for target in requested], skipped=[source for source in sources if source['status'] == 'skipped'])


def locked_source(value):
    required = {'software', 'repository', 'track', 'requested_ref', 'status',
                'source_ref', 'source_sha', 'source_version', 'target'}
    if (not isinstance(value, dict) or set(value) != required or value['software'] != 'deepmd-kit'
            or value['repository'] != 'deepmodeling/deepmd-kit' or value['status'] != 'resolved'
            or value['target'] not in GPU_TARGETS):
        raise ValueError('expected one exact resolved DeePMD source/target from the planning job')
    make_delivery(value, value['target'])
    return value


def query_status(python, run_id, controller="deepmd_standalone.py"):
    """Retry a dropped SSH status query without repeating job submission."""
    for attempt in range(3):
        response = python(controller, 'status', run_id,
                          capture_output=True, check=False)
        if response.returncode != 255 or attempt == 2:
            return response
        print(f'DEEPMD_STATUS_TRANSPORT_RETRY attempt={attempt + 1}', flush=True)
        time.sleep(5 * (attempt + 1))


def main():
    source = locked_source(json.loads(os.environ['DEEP_SOFTWARE_SOURCE']))
    delivery = make_delivery(source, source['target'])
    run_id = safe_name('dp-' + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT']
                       + '-' + source['track'] + '-' + source['target'] + '-' + source['source_sha'][:12])
    commit = safe_sha(os.environ['GITHUB_SHA'])
    user = safe_name(os.environ['REMOTE_USER'])
    temporary = Path(os.environ['RUNNER_TEMP'])
    results = temporary / 'results'
    results.mkdir(exist_ok=True)
    project = str(SAI_PROJECT_ROOT)
    root = project + '/experimental/deepmd-standalone'
    snapshot = root + '/controller/' + commit + '/' + run_id
    remote = user + '@c0.sai.ai-4s.com'
    options = ['-i', str(temporary / 'ssh/key'), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'UserKnownHostsFile=' + str(CONTROL.parent / '.ci/slurm/known_hosts'),
               '-o', 'ConnectTimeout=20', '-o', 'ServerAliveInterval=20', '-o', 'ServerAliveCountMax=6']

    def run(argv, **kwargs):
        kwargs.setdefault('check', True)
        return subprocess.run(list(map(str, argv)), text=True, **kwargs)

    def ssh(argv, **kwargs):
        return run(['ssh', *options, '-p', '12022', remote, shlex.join(list(map(str, argv)))], **kwargs)

    def upload(path, destination):
        run(['scp', '-q', *options, '-P', '12022', path, remote + ':' + destination], timeout=1800)

    def python(name, *args, **kwargs):
        return ssh(['env', 'SAI_SOFTWARE_ROOT=' + project, 'PYTHONDONTWRITEBYTECODE=1',
                    'python3', snapshot + '/' + name, *args], **kwargs)

    ssh(['mkdir', '-p', snapshot])
    archive = temporary / 'deepmd-controller.tar'
    with tarfile.open(archive, 'w') as payload:
        for name in (*FILES, 'deepmd_acceptance.py'):
            payload.add(CONTROL / name, arcname=name)
    upload(archive, snapshot + '/controller.tar')
    ssh(['tar', '-xf', snapshot + '/controller.tar', '-C', snapshot])
    ssh(['chmod', '0444', *[snapshot + '/' + name for name in (*FILES, 'deepmd_acceptance.py')]])
    digest = ssh(['python3', '-c', 'import sys; sys.path.insert(0, sys.argv[1]); '
        'from deepmd_standalone import fingerprint; print(fingerprint())', snapshot], capture_output=True).stdout.strip()
    if digest != delivery['identity']['recipe_sha256']:
        raise ValueError('transported controller differs from the checked-out GitHub recipe')
    # Reuse the same byte-qualified FUSE helper already provisioned for project
    # builds, with an independent copy in this controller's canonical root.
    prepare = '''from pathlib import Path
import fcntl, shutil, sys
sys.path.insert(0, sys.argv[1])
from cp2k_image_driver import DRIVER_SHA256, driver_path
from source_cache import checksum
project, root = Path(sys.argv[2]), Path(sys.argv[3])
assert (project / 'containers/base/minimal-v1.sif').is_file()
destination = driver_path(root)
destination.parent.mkdir(parents=True, exist_ok=True)
with (root / 'cache/tools/.provision.lock').open('a+') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    if not destination.exists():
        source = driver_path(project)
        if source.is_symlink() or checksum(source) != DRIVER_SHA256:
            raise ValueError('project qualified FUSE helper is missing or changed')
        with source.open('rb') as source_file, destination.open('xb') as target:
            shutil.copyfileobj(source_file, target)
        destination.chmod(0o555)
if destination.is_symlink() or checksum(destination) != DRIVER_SHA256 or destination.stat().st_mode & 0o222:
    raise ValueError('independent DeePMD helper differs from its qualified bytes')
'''
    ssh(['python3', '-c', prepare, snapshot, project, root])
    sha = source['source_sha']
    cache = root + '/cache/repositories/deepmd-kit'
    inventory = json.loads(python('source_cache.py', 'inventory', cache, '--commit', sha, capture_output=True).stdout)
    if sha not in inventory['cache_shas']:
        repository = temporary / 'deepmd-source.git'
        run(['git', 'clone', '--bare', 'https://github.com/deepmodeling/deepmd-kit.git', repository])
        run(['git', '-C', repository, 'fetch', '--no-tags', 'origin', sha])
        parts = temporary / 'deepmd-source-parts'
        pack(repository, sha, parts)
        destination = snapshot + '/input'
        ssh(['mkdir', '-p', destination])
        upload(parts / 'manifest.json', destination + '/manifest.json')
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda index: upload(parts / f'source.part.{index:02d}',
                destination + f'/source.part.{index:02d}'), range(8)))
        python('source_cache.py', 'receive', cache, destination)
    else:
        print('DEEPMD_SOURCE_CACHE_HIT', sha, flush=True)
    (results / 'delivery.json').write_text(json.dumps(delivery, sort_keys=True, indent=2) + '\n')
    retry = os.environ.get('RETRY_RELEASES', 'false')
    if retry not in ('true', 'false'):
        raise ValueError('retry_releases must be an explicit boolean')
    request = None
    recovery = None
    try:
        response = python('deepmd_standalone.py', 'submit', run_id, '--source', json.dumps(source),
            '--target', source['target'], *(['--retry-releases'] if retry == 'true' else []), capture_output=True)
        request = json.loads(response.stdout)
        (results / 'submission.json').write_text(json.dumps(request, sort_keys=True, indent=2) + '\n')
        if request.get('build') is False:
            print('DEEPMD_RELEASE_ALREADY_ATTEMPTED', json.dumps(request['skipped']), flush=True)
            return
        if request.get('delivery') != delivery or request.get('controller_sha256') != digest:
            raise ValueError('submitted DeePMD job differs from the locked source and recipe')
        deadline = time.monotonic() + 21000
        while time.monotonic() < deadline:
            response = (query_status(python, recovery['run_id'], 'deepmd_acceptance.py')
                        if recovery else query_status(python, run_id))
            if response.returncode not in (0, 1, 3):
                raise RuntimeError('DeePMD provenance/status check failed: ' + response.stderr)
            status = json.loads(response.stdout)
            (results / 'status.json').write_text(json.dumps(status, sort_keys=True, indent=2) + '\n')
            (results / 'ci-status.json').write_text(json.dumps(status, sort_keys=True, indent=2) + '\n')
            print(status['accounting'], 'build_verified=' + str(status.get('build_verified', bool(recovery))),
                  'scientific_verified=' + str(status['scientific_verified']), flush=True)
            if response.returncode == 0:
                if status['scientific_verified'] is not True:
                    raise ValueError('DeePMD completion lacks scientific acceptance')
                print('DEEPMD_INDEPENDENT_SIF_ACCEPTED_NOT_INSTALLED', flush=True)
                return
            if response.returncode == 1:
                if not recovery and status.get('build_verified'):
                    attempt = python('deepmd_acceptance.py', 'recover', run_id, capture_output=True, check=False)
                    if attempt.returncode == 0:
                        recovery = json.loads(attempt.stdout)
                        (results / 'acceptance-recovery.json').write_text(json.dumps(recovery, indent=2) + '\n')
                        print('DEEPMD_ECC_RECOVERY', recovery['run_id'], flush=True)
                        continue
                raise RuntimeError('DeePMD allocation failed; inspect the retained project run')
            time.sleep(30)
        raise TimeoutError('DeePMD observation deadline; retained Slurm job can still be monitored')
    finally:
        collect = '''from pathlib import Path
import sys, tarfile
task = Path(sys.argv[1])
if (task / 'results').is_dir():
    with tarfile.open(task / 'results/scientific-evidence.tar', 'w') as payload:
        for path in (task / 'runtime').glob('*'):
            if path.is_file() and not path.is_symlink() and path.suffix in ('.json', '.csv', '.nsys-rep', '.log', '.txt'):
                payload.add(path, arcname=path.name)
        for name in ('case', 'runtime-env.sh', 'training'):
            path = task / 'runtime' / name
            if path.exists() and not path.is_symlink():
                payload.add(path, arcname=name)
'''
        if request is None or 'job_id' in request:
            ssh(['python3', '-c', collect, root + '/runs/' + run_id], check=False)
            run(['scp', '-q', *options, '-P', '12022', '-r',
                 remote + ':' + root + '/runs/' + run_id + '/results/.', str(results)], check=False)
        if recovery:
            recovered = results / 'recovery'
            recovered.mkdir(exist_ok=True)
            ssh(['python3', '-c', collect, root + '/runs/' + recovery['run_id']], check=False)
            run(['scp', '-q', *options, '-P', '12022', '-r',
                 remote + ':' + root + '/runs/' + recovery['run_id'] + '/results/.', str(recovered)], check=False)


if __name__ == '__main__':
    main()
