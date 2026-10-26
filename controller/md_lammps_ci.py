#!/usr/bin/env python3
"""Actions entry for LAMMPS alone, reusing accepted installed DeePMD libraries."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import inspect
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time

from md_lammps_standalone import CONTROL, make_delivery, runtime_plan
from release_contract import resolve_tracks, validate_identity
from remote_controller import SAI_PROJECT_ROOT, safe_name, safe_sha
from source_cache import pack


def run(argv, **kwargs):
    return subprocess.run(list(map(str, argv)), check=True, text=True, **kwargs)


def selected_source():
    if os.environ.get('LAMMPS_SOURCE'):
        source = json.loads(os.environ['LAMMPS_SOURCE'])
        if not isinstance(source, dict):
            raise ValueError('LAMMPS_SOURCE must be a resolved source record')
        if 'target' in source:
            if source.pop('target') != os.environ.get('LAMMPS_TARGET'):
                raise ValueError('frozen LAMMPS matrix target differs from the selected partition')
        try:
            expected = resolve_tracks('lammps', [source['track']], resolver=lambda repository, ref: dict(
                sha=source['source_sha'], ref=source['source_ref'], version=source['source_version']))[0]
        except (KeyError, TypeError) as error:
            raise ValueError('incomplete frozen LAMMPS source') from error
        if source != expected or (source['track'] == 'development' and source['source_ref'] != 'develop'):
            raise ValueError('frozen LAMMPS source differs from the selected upstream channel')
    else:
        source = resolve_tracks('lammps', [os.environ.get('LAMMPS_TRACK', 'development')])[0]
    if source['status'] != 'resolved':
        raise ValueError('selected LAMMPS source is unavailable')
    return source


def validate_handoff(handoff, source, target):
    """Bind a downloaded artifact to this matrix row, including on job reruns."""
    fields = {'schema', 'workflow_run_id', 'controller_sha', 'run_id', 'source', 'delivery',
              'controller_files', 'decision', 'build_status'}
    if (not isinstance(handoff, dict) or set(handoff) != fields or type(handoff['schema']) is not int
            or handoff['schema'] != 1 or handoff['workflow_run_id'] != os.environ['GITHUB_RUN_ID']
            or handoff['controller_sha'] != safe_sha(os.environ['GITHUB_SHA']) or handoff['source'] != source):
        raise ValueError('LAMMPS handoff differs from this workflow/controller/source')
    run_id = safe_name(handoff['run_id'])
    pattern = (r'lmp-' + re.escape(os.environ['GITHUB_RUN_ID']) + r'-([1-9][0-9]*)-'
               + re.escape(source['track'] + '-' + target) + r'-[0-9]{8}-' + source['source_sha'][:12])
    match = re.fullmatch(pattern, run_id)
    if not match or int(match[1]) > int(os.environ['GITHUB_RUN_ATTEMPT']):
        raise ValueError('LAMMPS handoff run differs from this workflow/source/target')
    safe_name(run_id + '-science')
    identity = validate_identity(handoff['delivery']['identity'])
    expected = {name: source[name] for name in ('software', 'repository', 'track', 'source_ref', 'source_sha', 'source_version')}
    if any(identity[name] != value for name, value in dict(expected, target=target).items()):
        raise ValueError('LAMMPS handoff delivery differs from its frozen source/target')
    pins = handoff['controller_files']
    required = {'md_lammps_standalone.py', 'lammps_acceptance.py', 'lammps_rank.sh', 'lammps_verify.sbatch',
                'runtime-plan.json', 'native-reference.json', 'delivery.json', 'remote_controller.py'}
    if (not isinstance(pins, dict) or not required.issubset(pins) or
            any(not isinstance(name, str) or Path(name).name != name or
                not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha) for name, sha in pins.items())):
        raise ValueError('invalid frozen LAMMPS controller file pins')
    from lammps_acceptance import REFERENCE_SHA256
    if pins['native-reference.json'] != REFERENCE_SHA256:
        raise ValueError('LAMMPS handoff changed its accepted reference')
    decision = handoff['decision']
    if not isinstance(decision, dict) or type(decision.get('build')) is not bool or type(decision.get('retry')) is not bool:
        raise ValueError('invalid LAMMPS build-attempt decision')
    expected = dict(schema=1, run_id=run_id, retry=decision['retry'], build=decision['build'],
                    identities=[identity] if decision['build'] else [], skipped=[],
                    meaning='build attempt only; no artifact or acceptance claim')
    if not decision['build']:
        skipped = decision.get('skipped')
        if (source['track'] == 'development' or decision['retry'] or not isinstance(skipped, list) or len(skipped) != 1):
            raise ValueError('only a recorded unchanged release may skip LAMMPS science')
        expected['skipped'] = [dict(identity=identity, previous_run=safe_name(skipped[0]['previous_run']))]
    if decision != expected:
        raise ValueError('LAMMPS handoff differs from its build-attempt decision')
    status = handoff['build_status']
    if decision['build']:
        artifact = str(SAI_PROJECT_ROOT) + '/experimental/deepmd-lammps/runs/' + run_id + '/candidate.sif'
        if (not isinstance(status, dict) or status.get('delivery') != handoff['delivery'] or
                status.get('artifact') != artifact or status.get('build_verified') is not True or
                status.get('state') != 'COMPLETED' or status.get('exit_code') != '0:0' or
                not re.fullmatch('[0-9a-f]{64}', str(status.get('artifact_sha256', '')))):
            raise ValueError('LAMMPS handoff has no verified build SIF identity')
    elif status is not None:
        raise ValueError('an unchanged release is not a new accepted build')
    return handoff


def inspect_handoff(project, handoff, continuing):
    """Read-only remote preflight using the original frozen acceptance helpers."""
    import hashlib
    import json
    import os
    from pathlib import Path
    import re
    import sys

    project = Path(project)
    root = project / 'experimental/deepmd-lammps'
    control = root / 'controller' / handoff['controller_sha'] / handoff['run_id']
    build = root / 'runs' / handoff['run_id']
    output = root / 'runtime-tests' / (handoff['run_id'] + '-science')
    for path in (project, root, control, build, output):
        if not path.is_absolute() or path.resolve() != path or path.is_symlink():
            raise ValueError('untrusted LAMMPS continuation path')

    def read(path):
        if not path.is_file() or path.resolve() != path or path.is_symlink():
            raise ValueError('incomplete previous submission or redirected metadata: ' + str(path))
        return path.read_text()

    # Check the transported snapshot before importing any of its helpers.
    for name, sha in handoff['controller_files'].items():
        path = control / name
        if (Path(name).name != name or path.resolve() != path or not path.is_file() or
                hashlib.sha256(path.read_bytes()).hexdigest() != sha):
            raise ValueError('frozen LAMMPS controller changed: ' + name)
    if continuing and json.loads(read(control / 'ci-handoff.json')) != handoff:
        raise ValueError('immutable remote LAMMPS handoff changed')
    if json.loads(read(build / 'input/build-attempt.json')) != handoff['decision']:
        raise ValueError('recorded LAMMPS build-attempt decision changed')
    if json.loads(read(control / 'delivery.json')) != handoff['delivery']:
        raise ValueError('frozen LAMMPS delivery changed')
    os.environ['SAI_SOFTWARE_ROOT'] = str(project)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(control))
    import lammps_acceptance as acceptance
    from md_lammps_standalone import validate_delivery
    validate_delivery(handoff['delivery'])
    if not handoff['decision']['build']:
        if output.exists():
            raise ValueError('skipped release unexpectedly has a scientific submission')
        return dict(build_status=None, science_exists=False)
    status = acceptance.build_status(build)
    if (json.loads(read(build / 'request.json'))['controller'] != str(control) or
            status['delivery'] != handoff['delivery'] or
            (continuing and status != handoff['build_status'])):
        raise ValueError('build/controller/SIF identity differs from the immutable LAMMPS handoff')
    exists = continuing and output.exists()
    if exists:
        for name in ('request.json', 'request.sha256', 'job.id', 'job.sbatch'):
            read(output / name)
        job = read(output / 'job.id').strip()
        if not re.fullmatch('[1-9][0-9]*', job):
            raise ValueError('invalid recorded LAMMPS science Slurm handle')
        # context verifies every frozen reference, helper, input and job-script
        # hash. Final verify still replays all fourteen numerical cases.
        request, _, identity, _ = acceptance.context(output)
        if (request['build'] != str(build) or request['build_status'] != status or
                request['expected_delivery'] != handoff['delivery'] or
                request['build_request_sha256'] != acceptance.digest(build / 'request.json') or
                request['reference_root'] != str(acceptance.REFERENCE_ROOT)):
            raise ValueError('existing science differs from the immutable LAMMPS handoff')
        for name in ('lammps_acceptance.py', 'lammps_rank.sh', 'lammps_verify.sbatch', 'remote_controller.py'):
            if request['files'].get('control/' + name) != handoff['controller_files'][name]:
                raise ValueError('existing LAMMPS science controller changed')
        if read(output / 'job.sbatch') != acceptance.render(output, identity['partition']):
            raise ValueError('existing LAMMPS science script changed')
    return dict(build_status=status, science_exists=bool(exists))


def remote_handoff(ssh, project, handoff, *, continuing):
    code = inspect.getsource(inspect_handoff) + (
        '\nimport json, sys\nprint(json.dumps(inspect_handoff(sys.argv[1], json.loads(sys.argv[2]), sys.argv[3] == "science")))\n')
    response = json.loads(ssh(['python3', '-I', '-B', '-c', code, project,
                              json.dumps(handoff, sort_keys=True), 'science' if continuing else 'build'],
                             capture_output=True).stdout)
    if (not isinstance(response, dict) or set(response) != {'build_status', 'science_exists'} or
            type(response['science_exists']) is not bool):
        raise ValueError('invalid LAMMPS continuation preflight response')
    return response


def main():
    from lammps_acceptance import REFERENCE_SHA256, digest
    phase = os.environ.get('LAMMPS_PHASE', 'all')
    if phase not in ('all', 'build', 'science'):
        raise ValueError('unknown LAMMPS CI phase')
    if phase == 'science' and not os.environ.get('LAMMPS_SOURCE'):
        raise ValueError('LAMMPS science requires the original frozen matrix source')
    source = selected_source()
    target = os.environ['LAMMPS_TARGET']
    if phase == 'science':
        handoff = validate_handoff(json.loads(Path(os.environ['LAMMPS_HANDOFF']).read_text()), source, target)
        delivery, run_id = handoff['delivery'], handoff['run_id']
    else:
        delivery = make_delivery(source, target)
        run_id = safe_name('lmp-' + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT'] +
                           '-' + source['track'] + '-' + target + '-' +
                           datetime.now(timezone.utc).strftime('%Y%m%d') + '-' + source['source_sha'][:12])
    safe_name(run_id + '-science')
    retry = os.environ.get('RETRY_RELEASES', 'false')
    if retry not in ('', 'false', 'true'):
        raise ValueError('RETRY_RELEASES must be an explicit boolean')
    user = safe_name(os.environ['REMOTE_USER'])
    commit = safe_sha(os.environ['GITHUB_SHA'])
    temporary = Path(os.environ['RUNNER_TEMP'])
    results = temporary / 'results'
    results.mkdir(exist_ok=True)
    project = str(SAI_PROJECT_ROOT)
    root = project + '/experimental/deepmd-lammps'
    snapshot = root + '/controller/' + commit + '/' + run_id
    remote = user + '@c0.sai.ai-4s.com'
    options = ['-i', str(temporary / 'ssh/key'), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'UserKnownHostsFile=' + str(CONTROL.parent / '.ci/slurm/known_hosts'),
               '-o', 'ConnectTimeout=20', '-o', 'ServerAliveInterval=20', '-o', 'ServerAliveCountMax=6']

    def ssh(argv, **kwargs):
        return run(['ssh', *options, '-p', '12022', remote, shlex.join(list(map(str, argv)))], **kwargs)

    def upload(path, destination):
        sources = list(path) if isinstance(path, (list, tuple)) else [path]
        for attempt in range(3):
            try:
                return run(['scp', '-q', *options, '-P', '12022', *sources, remote + ':' + destination], timeout=1800)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                if attempt == 2:
                    raise
                time.sleep(5 * (attempt + 1))

    def python(name, *args, **kwargs):
        return ssh(['env', 'SAI_SOFTWARE_ROOT=' + project,
                    'python3', snapshot + '/' + name, *args], **kwargs)

    reference = CONTROL.parent / 'deployment/lammps-30Sep2026/native-reference.json'
    if digest(reference) != REFERENCE_SHA256:
        raise ValueError('the committed accepted LAMMPS reference changed')
    request = results / 'delivery.json'
    request.write_text(json.dumps(delivery, sort_keys=True) + '\n')
    handoff_path = results / 'ci-handoff.json'

    def collect_build():
        subprocess.run(['scp', '-q', *options, '-P', '12022', '-r',
                        remote + ':' + root + '/runs/' + run_id + '/results/.', str(results)], check=False)

    if phase != 'science':
        ssh(['mkdir', '-p', snapshot])
        files = [p for p in CONTROL.glob('md_*.*') if p.suffix in ('.py', '.sh', '.json')]
        files += [CONTROL / name for name in ('remote_controller.py', 'source_cache.py', 'resolve_source.py',
                  'create_rootfs.sh', 'release_contract.py', 'native_module.py', 'export_native.py',
                  'module_publication.py', 'delivery_layout.py', 'cp2k_image_driver.py', 'cp2k_overlay_sync.py',
                  'lammps_acceptance.py', 'lammps_rank.sh', 'lammps_verify.sbatch')]
        files += [runtime_plan(), reference, request]
        upload(files, snapshot + '/')
        decision = json.loads(python('release_contract.py', root, run_id, json.dumps([delivery['identity']]),
                                     *(['--retry'] if retry == 'true' else []), capture_output=True).stdout)
        (results / 'build-attempt.json').write_text(json.dumps(decision, sort_keys=True) + '\n')
        handoff = dict(schema=1, workflow_run_id=os.environ['GITHUB_RUN_ID'], controller_sha=commit,
                       run_id=run_id, source=source, delivery=delivery, decision=decision,
                       controller_files={path.name: digest(path) for path in files}, build_status=None)
        if decision['build']:
            if decision['identities'] != [delivery['identity']]:
                raise ValueError('claimed LAMMPS source differs from the requested delivery')
            for name, identity in (('lammps', delivery['identity']), ('deepmd-kit', delivery['deepmd_dependency']['identity'])):
                sha = identity['source_sha']
                cache = root + '/cache/repositories/' + name
                legacy = '/home/stardust/sai-hpc-software/experimental/deepmd-lammps/cache/repositories/' + name
                python('source_cache.py', 'seed', cache, legacy, capture_output=True)
                inventory = json.loads(python('source_cache.py', 'inventory', cache, '--commit', sha, capture_output=True).stdout)
                if sha in inventory['cache_shas']:
                    print('SOURCE_CACHE_HIT', name, sha, flush=True)
                    continue
                repository = temporary / (name + '.git')
                run(['git', 'clone', '--bare', 'https://github.com/' + identity['repository'] + '.git', repository])
                run(['git', '--git-dir', repository, 'fetch', '--no-tags', 'origin', sha])
                parts = temporary / ('parts-' + name)
                base = None
                for candidate in inventory['cache_shas']:
                    ancestor = subprocess.run(['git', '-C', str(repository), 'merge-base', '--is-ancestor', candidate, sha],
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    if ancestor.returncode == 0 and candidate != sha:
                        base = candidate
                        break
                manifest = pack(repository, sha, parts, base)
                print('SOURCE_TRANSFER', name, 'base=' + str(base),
                      'compressed_bytes=' + str(manifest['compressed_size']), flush=True)
                destination = snapshot + '/input/' + name
                ssh(['mkdir', '-p', destination])
                upload(parts / 'manifest.json', destination + '/manifest.json')
                with ThreadPoolExecutor(max_workers=8) as executor:
                    list(executor.map(lambda i: upload(parts / f'source.part.{i:02d}', destination + f'/source.part.{i:02d}'), range(8)))
                python('source_cache.py', 'receive', cache, destination)
            try:
                python('md_lammps_standalone.py', 'submit', run_id, snapshot + '/delivery.json')
                python('md_lammps_standalone.py', 'monitor', run_id)
                handoff['build_status'] = remote_handoff(ssh, project, handoff, continuing=False)['build_status']
            finally:
                collect_build()
        validate_handoff(handoff, source, target)
        handoff_path.write_text(json.dumps(handoff, sort_keys=True) + '\n')
        upload(handoff_path, snapshot + '/ci-handoff.json')
        if phase == 'build' and decision['build']:
            print('LAMMPS_BUILD_HANDOFF_READY: scientific acceptance runs in its own Actions job', flush=True)
            return
    else:
        handoff_path.write_text(json.dumps(handoff, sort_keys=True) + '\n')
        (results / 'build-attempt.json').write_text(json.dumps(handoff['decision'], sort_keys=True) + '\n')

    science = root + '/runtime-tests/' + run_id + '-science'
    try:
        existing = remote_handoff(ssh, project, handoff, continuing=True)['science_exists']
        if not handoff['decision']['build']:
            print('UNCHANGED_RELEASE_SKIPPED: existing attempt; no new scientific acceptance claimed', flush=True)
            return
        if not existing:
            python('lammps_acceptance.py', 'submit', root + '/runs/' + run_id,
                   '--run-id', run_id + '-science', '--reference', snapshot + '/native-reference.json',
                   '--delivery', snapshot + '/delivery.json')
        python('lammps_acceptance.py', 'monitor', science)
        python('lammps_acceptance.py', 'verify', science)
    finally:
        if phase == 'science':
            collect_build()
        science_results = results / 'science'
        science_results.mkdir(exist_ok=True)
        # Scratch lives in PROJECT/runtime, outside this complete evidence tree.
        subprocess.run(['scp', '-q', *options, '-P', '12022', '-r',
                        remote + ':' + science + '/.', str(science_results)], check=False)
    print('LAMMPS_GPU_SCIENCE_ACCEPTED: native installation and publication remain separate', flush=True)


if __name__ == '__main__':
    main()
