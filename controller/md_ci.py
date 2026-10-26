#!/usr/bin/env python3
"""Runner-side paired builds, raw scientific/performance gates and publication."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import inspect
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
from md_tracking import REPOSITORIES, fingerprint, identify_track_pair
from remote_controller import safe_name, safe_sha, SAI_PROJECT_ROOT
from source_cache import pack


def run(argv, **kwargs):
    try:
        return subprocess.run(list(map(str, argv)), check=True, text=True, **kwargs)
    except subprocess.CalledProcessError as error:
        # Machine-readable stdout stays captured; failed remote diagnostics do not.
        detail = (error.stderr or error.stdout or '').strip()
        if detail:
            print(detail, file=__import__('sys').stderr, flush=True)
            results = Path(os.environ['RUNNER_TEMP']) / 'results'
            results.mkdir(parents=True, exist_ok=True)
            with (results / 'command-errors.log').open('a') as stream:
                stream.write(detail + '\n')
        raise


def inspect_existing_acceptance(root, control, build_run, stage, delivery, performance_scope='all'):
    """Read-only preflight for resuming a previously submitted Slurm phase.

    This function is sent verbatim to the frozen remote snapshot. A submission
    without its job handle is ambiguous and must never be silently repeated.
    """
    import importlib
    import json
    import os
    from pathlib import Path
    import sys

    root, control = Path(root), Path(control)
    if stage not in ('science', 'performance', 'performance-retest'):
        raise ValueError('unknown MD acceptance continuation stage')
    for path in (root, control):
        if not path.is_absolute() or path.resolve() != path or path.is_symlink():
            raise ValueError('untrusted MD continuation path')
    if not control.is_relative_to(root / 'controller'):
        raise ValueError('MD continuation escaped its immutable controller snapshot')
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(control))
    os.environ['SAI_SOFTWARE_ROOT'] = str(root.parent.parent)
    from md_controller import artifact_path, validate_pair, validate_runtime_image
    from md_tracking import fingerprint
    from remote_controller import safe_name
    from source_cache import checksum

    build_run = safe_name(build_run)
    run_id = safe_name(build_run + '-' + stage)
    directory = root / 'runtime-tests' / run_id
    if directory.resolve() != directory or directory.is_symlink():
        raise ValueError('untrusted MD continuation task path')
    if not directory.exists():
        return False

    def regular(path):
        if not path.is_file() or path.is_symlink() or path.resolve() != path:
            raise ValueError('incomplete or untrusted MD submission; recover its recorded Slurm handle before continuing')
        return path

    request = json.loads(regular(directory / 'request.json').read_text())
    job = regular(directory / 'job.id').read_text().strip()
    script = regular(directory / 'job.sbatch')
    if not job.isdigit() or int(job) <= 0 or ('job' in request and request['job'] != job):
        raise ValueError('invalid MD continuation Slurm handle')
    delivery = validate_pair(delivery)
    if delivery['recipe_sha256'] != fingerprint(control):
        raise ValueError('MD continuation snapshot recipe changed')
    build = root / 'runs' / build_run
    original = json.loads(regular(build / 'request.json').read_text())
    status = json.loads(regular(build / 'results/status.json').read_text())
    image = artifact_path(root, delivery, build_run)
    validate_runtime_image(image, delivery)
    digest = checksum(image)
    if (original.get('run_id') != build_run or original.get('delivery') != delivery
            or original.get('controller') != str(control)
            or original.get('job_script_sha256') != checksum(regular(build / 'job.sbatch'))
            or status.get('state') != 'COMPLETED' or status.get('exit_code') != '0:0'
            or status.get('build_verified') is not True or status.get('delivery') != delivery
            or status.get('artifact') != str(image) or status.get('artifact_sha256') != digest):
        raise ValueError('MD continuation build differs from the verified immutable handoff')
    expected = dict(run_id=run_id, build_run=build_run, delivery=delivery,
                    artifact=str(image), artifact_sha256=digest, target=delivery['plan']['target'])
    if stage == 'science':
        expected.update(nodes=2, ranks=4, sources=delivery['plan']['sources'],
                        version=delivery['plan']['version'], acceptance_recipe_sha256=delivery['recipe_sha256'])
        controller = importlib.import_module('md_acceptance_controller')
    else:
        controller = importlib.import_module('md_performance_controller')
        if request.get('performance_scope', 'all') != performance_scope:
            raise ValueError('existing MD performance scope differs from the requested continuation')
        expected.update(science_run=build_run + '-science', resources=controller.RESOURCES,
                        recipe_sha256=delivery['recipe_sha256'])
        repeat = request.get('repeat_of')
        if (stage == 'performance' and repeat) or (stage == 'performance-retest' and (
                not isinstance(repeat, dict) or repeat.get('run_id') != build_run + '-performance')):
            raise ValueError('MD continuation changed its single permitted performance repeat')
    if any(request.get(key) != value for key, value in expected.items()):
        raise ValueError('existing MD acceptance differs from the immutable CI handoff')
    if (request.get('job_script_sha256') != checksum(script)
            or script.read_text() != controller.render(request, directory)):
        raise ValueError('existing MD acceptance script changed')
    status_path = directory / 'results/status.json'
    if status_path.exists() or status_path.is_symlink():
        status = json.loads(regular(status_path).read_text())
        if status.get('job') != job:
            raise ValueError('MD continuation status belongs to another Slurm job')
        if (status.get('state') == 'COMPLETED' and status.get('exit_code') == '0:0'
                and status.get('evidence_sha256')):
            evidence = regular(directory / 'results/evidence.json')
            if checksum(evidence) != status['evidence_sha256']:
                raise ValueError('MD continuation evidence differs from its completed receipt')
            # `verify` re-reads the raw evidence without rewriting the original
            # status/evidence bytes pinned by an already-submitted repeat.
            return 'completed'
    return True


def existing_acceptance(ssh, root, control, build_run, stage, delivery, *, performance_scope='all'):
    code = inspect.getsource(inspect_existing_acceptance) + (
        '\nimport json, sys\nprint(json.dumps(inspect_existing_acceptance(*sys.argv[1:5], json.loads(sys.argv[5]), sys.argv[6])))\n')
    result = ssh(['python3', '-c', code, root, control, build_run, stage,
                  json.dumps(delivery, sort_keys=True), performance_scope], capture_output=True)
    found = json.loads(result.stdout)
    if type(found) is not bool and found != 'completed':
        raise ValueError('invalid MD continuation preflight response')
    return found


def run_performance_gate(remote_python, run_id, science_run, existing=None, *, scope='all'):
    """Repeat exactly once only after verified raw evidence exceeds 10% slowdown."""
    if scope not in ('all', 'gpu'):
        raise ValueError('unknown MD performance scope')
    for suffix in ('-performance', '-performance-retest'):
        performance_run = safe_name(run_id + suffix)
        previous = existing(suffix.removeprefix('-')) if existing is not None else False
        if not previous:
            remote_python('md_performance_controller.py', 'submit', performance_run, run_id, science_run,
                          *(['--scope', scope] if scope != 'all' else []))
        try:
            remote_python('md_performance_controller.py', 'verify' if previous == 'completed' else 'monitor', performance_run)
        except subprocess.CalledProcessError as error:
            if error.returncode != 3 or suffix == '-performance-retest':
                raise
            print('MD_PERFORMANCE_RETEST: raw-verified slowdown exceeds 10%; repeating identical candidate', flush=True)
        else:
            return performance_run
    raise RuntimeError('MD performance gate did not produce an accepted run')


def run_acceptance_gate(remote_python, run_id, existing):
    """Resume fixed science/performance tasks, then revalidate publication."""
    science_run = safe_name(run_id + '-science')
    previous = existing('science')
    if not previous:
        remote_python('md_acceptance_controller.py', 'submit', science_run, run_id, '--nodes', '2')
    remote_python('md_acceptance_controller.py', 'verify' if previous == 'completed' else 'monitor', science_run)
    performance_run = run_performance_gate(remote_python, run_id, science_run, existing=existing, scope='gpu')
    remote_python('md_publication.py', 'publish', run_id, science_run, performance_run)
    remote_python('md_publication.py', 'verify', run_id)
    return performance_run


def main():
    phase = os.environ.get('MD_PHASE', 'all')
    if phase not in ('all', 'deepmd', 'lammps', 'acceptance'):
        raise ValueError('unknown MD CI phase')
    plan = json.loads(os.environ['MD_PAIR'])
    control = Path(__file__).resolve().parent
    recipe = fingerprint(control)
    request = dict(schema=2, plan=plan, recipe_sha256=recipe, identities=identify_track_pair(plan, recipe))
    from md_controller import validate_pair
    validate_pair(request)
    retry = os.environ.get('RETRY_RELEASES') == 'true'
    user = safe_name(os.environ['REMOTE_USER'])
    sha = safe_sha(os.environ['GITHUB_SHA'])
    run_id = safe_name('-'.join(('md', os.environ['GITHUB_RUN_ID'], os.environ['GITHUB_RUN_ATTEMPT'],
                                 datetime.now(timezone.utc).date().isoformat(), plan['selection_sha256'][:16])))
    if phase in ('lammps', 'acceptance'):
        run_id = safe_name(os.environ['MD_RUN_ID'])
        pattern = (r'md-' + re.escape(os.environ['GITHUB_RUN_ID']) + r'-[0-9]+-\d{4}-\d{2}-\d{2}-'
                   + re.escape(plan['selection_sha256'][:16]))
        if not re.fullmatch(pattern, run_id):
            raise ValueError('MD handoff does not belong to this workflow and source pair')
    safe_name(run_id + '-science')
    temporary = Path(os.environ['RUNNER_TEMP'])
    project = str(SAI_PROJECT_ROOT)
    root = project + '/experimental/deepmd-lammps'
    snapshot = root + f'/controller/{sha}/{run_id}'
    remote_task = root + '/runs/' + run_id
    results = temporary / 'results'
    results.mkdir(exist_ok=True)
    remote = user + '@c0.sai.ai-4s.com'
    options = ['-i', str(temporary / 'ssh/key'), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'UserKnownHostsFile=' + str(control.parent / '.ci/slurm/known_hosts'),
               '-o', 'ConnectTimeout=20', '-o', 'ServerAliveInterval=20', '-o', 'ServerAliveCountMax=6']
    def ssh(argv, **kwargs):
        return run(['ssh', *options, '-p', '12022', remote, shlex.join(list(map(str, argv)))], **kwargs)
    def upload(source, destination):
        sources = list(source) if isinstance(source, (list, tuple)) else [source]
        return run(['scp', '-q', *options, '-P', '12022', *sources, remote + ':' + destination], timeout=1800)
    def python(name, *args, **kwargs):
        return ssh(['env', 'SAI_SOFTWARE_ROOT=' + project, 'python3', snapshot + '/' + name, *args], **kwargs)
    def collect(stage=None):
        source = root + '/runtime-tests/' + run_id + '-' + stage if stage else remote_task
        destination = results / stage if stage else results
        destination.mkdir(exist_ok=True)
        subprocess.run(['scp', '-q', *options, '-P', '12022', '-r',
                        remote + ':' + source + '/results/.', str(destination)], check=False)
    def accept():
        def existing(stage):
            return existing_acceptance(ssh, root, snapshot, run_id, stage, request, performance_scope='gpu')
        run_acceptance_gate(python, run_id, existing)
        run(['scp', '-q', *options, '-P', '12022',
             remote + ':' + remote_task + '/results/publication.json', results / 'publication.json'])
    def collect_acceptance():
        for stage in ('science', 'performance', 'performance-retest'):
            collect(stage)
    if phase in ('lammps', 'acceptance'):
        # No upload, claim or resubmission here. Continue the immutable snapshot
        # created by the first job, even when a later Actions job is retried.
        observed = json.loads(ssh(['cat', remote_task + '/input/pair.json'], capture_output=True).stdout)
        validate_pair(observed)
        if observed['recipe_sha256'] != recipe or observed['identities'] != request['identities']:
            raise ValueError('MD handoff differs from the current recipe or source identities')
        request = observed
        try:
            if phase == 'lammps':
                python('md_controller.py', 'monitor', run_id, '--timeout', '20700')
                run(['scp', '-q', *options, '-P', '12022', remote + ':' + remote_task + '/artifact.path', results / 'artifact.path'])
            else:
                accept()
        finally:
            if phase == 'acceptance':
                collect_acceptance()
            else:
                collect()
        print('MD_DELIVERY_PUBLISHED' if phase == 'acceptance' else 'MD_BUILD_VERIFIED_AWAITING_ACCEPTANCE', flush=True)
        return
    ssh(['mkdir', '-p', snapshot, remote_task + '/input', remote_task + '/results'])
    files = [path for path in control.glob('md_*.*') if path.suffix in ('.py', '.sh', '.json')]
    files += [control / name for name in ('remote_controller.py', 'source_cache.py', 'resolve_source.py',
                                          'create_rootfs.sh', 'release_contract.py', 'native_module.py', 'export_native.py',
                                          'module_publication.py', 'delivery_layout.py', 'cp2k_image_driver.py',
                                          'cp2k_overlay_sync.py')]
    upload(files, snapshot + '/')
    primaries = [request['identities'][trigger['software']] for trigger in plan['triggers']]
    decision = json.loads(python('release_contract.py', root, run_id, json.dumps(primaries, sort_keys=True),
                                 *(['--retry'] if retry else []), capture_output=True).stdout)
    (results / 'build-attempt.json').write_text(json.dumps(decision, sort_keys=True) + '\n')
    selected = decision['identities']
    if not decision['build']:
        print('MD_BUILD_SKIPPED: latest primary versions already attempted; explicit retry required; no acceptance claimed', flush=True)
        return
    if phase == 'deepmd':
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write('run_id=' + run_id + '\n')
    plan = dict(plan, triggers=[trigger for trigger in plan['triggers']
                               if request['identities'][trigger['software']] in selected])
    request = validate_pair(dict(request, plan=plan))
    pair_file = temporary / 'pair.json'
    pair_file.write_text(json.dumps(request, sort_keys=True) + '\n')
    upload(pair_file, remote_task + '/input/pair.json')
    (results / 'delivery.json').write_text(json.dumps(request, sort_keys=True) + '\n')
    for name, source in plan['sources'].items():
        cache = root + '/cache/repositories/' + name
        inventory = json.loads(python('source_cache.py', 'inventory', cache, '--commit',
                                      source['sha'], capture_output=True).stdout)
        if source['sha'] in inventory['cache_shas']:
            print(f'SOURCE_CACHE_HIT {name} {source["sha"]}', flush=True)
            continue
        repository = temporary / (name + '.git')
        # Only bare object storage on the runner and host; checkout/build are
        # restricted to the overlay. Upstream repositories are read-only inputs.
        run(['git', 'clone', '--bare', 'https://github.com/' + REPOSITORIES[name] + '.git', repository])
        run(['git', '--git-dir', repository, 'fetch', '--no-tags', 'origin', source['sha']])
        base = None
        for candidate in inventory['cache_shas']:
            if subprocess.run(['git', '--git-dir', str(repository), 'merge-base', '--is-ancestor', candidate, source['sha']],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                base = candidate
                break
        parts = temporary / ('parts-' + name)
        manifest = pack(repository, source['sha'], parts, base)
        destination = remote_task + '/input/' + name
        ssh(['mkdir', '-p', destination])
        upload(parts / 'manifest.json', destination + '/manifest.json')
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda index: upload(parts / f'source.part.{index:02d}', destination + f'/source.part.{index:02d}'), range(8)))
        python('source_cache.py', 'receive', cache, destination)
        print(f'SOURCE_TRANSFER {name} {manifest["compressed_size"]} bytes', flush=True)
    ssh(['test', '-s', project + '/containers/base/minimal-v1.sif'])
    try:
        python('md_controller.py', 'submit', run_id, remote_task + '/input/pair.json')
        if phase == 'deepmd':
            python('md_controller.py', 'monitor', run_id, '--stage', 'deepmd', '--timeout', '14400')
            return
        python('md_controller.py', 'monitor', run_id)
        run(['scp', '-q', *options, '-P', '12022', remote + ':' + remote_task + '/artifact.path', results / 'artifact.path'])
        accept()
    finally:
        collect()
        if phase == 'all':
            collect_acceptance()
    print('MD_DELIVERY_PUBLISHED', flush=True)


if __name__ == '__main__':
    main()
