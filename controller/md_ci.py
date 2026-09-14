#!/usr/bin/env python3
"""Runner-side source transport and isolated experimental candidate execution."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import subprocess
from md_tracking import REPOSITORIES, fingerprint, identify_track_pair
from remote_controller import safe_name, safe_sha
from source_cache import pack


def run(argv, **kwargs):
    return subprocess.run(list(map(str, argv)), check=True, text=True, **kwargs)


def main():
    plan = json.loads(os.environ['MD_PAIR'])
    control = Path(__file__).resolve().parent
    recipe = fingerprint(control)
    request = dict(schema=2, plan=plan, recipe_sha256=recipe, identities=identify_track_pair(plan, recipe))
    from md_controller import validate_pair
    validate_pair(request)
    retry = os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch' and os.environ.get('RETRY_RELEASES') == 'true'
    user = safe_name(os.environ['REMOTE_USER'])
    sha = safe_sha(os.environ['GITHUB_SHA'])
    run_id = safe_name('-'.join(('md', os.environ['GITHUB_RUN_ID'], os.environ['GITHUB_RUN_ATTEMPT'],
                                 datetime.now(timezone.utc).date().isoformat(), plan['selection_sha256'][:16])))
    safe_name(run_id + '-science')
    temporary = Path(os.environ['RUNNER_TEMP'])
    project = f'/home/{user}/sai-hpc-software'
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
        return run(['scp', '-q', *options, '-P', '12022', source, remote + ':' + destination], timeout=1800)
    def python(name, *args, **kwargs):
        return ssh(['python3', snapshot + '/' + name, *args], **kwargs)
    ssh(['mkdir', '-p', snapshot, remote_task + '/input', remote_task + '/results'])
    files = [path for path in control.glob('md_*.*') if path.suffix in ('.py', '.sh', '.json')]
    files += [control / name for name in ('remote_controller.py', 'source_cache.py', 'resolve_source.py',
                                          'create_rootfs.sh', 'release_contract.py', 'native_module.py', 'export_native.py')]
    for path in files:
        upload(path, snapshot + '/' + path.name)
    primaries = [request['identities'][trigger['software']] for trigger in plan['triggers']]
    decision = json.loads(python('release_contract.py', root, run_id, json.dumps(primaries, sort_keys=True),
                                 *(['--retry'] if retry else []), capture_output=True).stdout)
    (results / 'build-attempt.json').write_text(json.dumps(decision, sort_keys=True) + '\n')
    selected = decision['identities']
    if not decision['build']:
        print('MD_BUILD_SKIPPED: latest primary versions already attempted; explicit retry required; no acceptance claimed', flush=True)
        return
    plan = dict(plan, triggers=[trigger for trigger in plan['triggers']
                               if request['identities'][trigger['software']] in selected])
    request = validate_pair(dict(request, plan=plan))
    pair_file = temporary / 'pair.json'
    pair_file.write_text(json.dumps(request, sort_keys=True) + '\n')
    upload(pair_file, remote_task + '/input/pair.json')
    (results / 'delivery.json').write_text(json.dumps(request, sort_keys=True) + '\n')
    for name, source in plan['sources'].items():
        cache = root + '/cache/repositories/' + name
        inventory = json.loads(python('source_cache.py', 'inventory', cache, capture_output=True).stdout)
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
        python('md_controller.py', 'monitor', run_id)
        run(['scp', '-q', *options, '-P', '12022', remote + ':' + remote_task + '/artifact.path', results / 'artifact.path'])
        science_run = safe_name(run_id + '-science')
        python('md_acceptance_controller.py', 'submit', science_run, run_id)
        python('md_acceptance_controller.py', 'monitor', science_run)
    finally:
        subprocess.run(['scp', '-q', *options, '-P', '12022', '-r', remote + ':' + remote_task + '/results/.', str(results)], check=False)
        science_results = results / 'science'
        science_results.mkdir(exist_ok=True)
        subprocess.run(['scp', '-q', *options, '-P', '12022', '-r',
                        remote + ':' + root + '/runtime-tests/' + run_id + '-science/results/.',
                        str(science_results)], check=False)
    print('EXPERIMENTAL CANDIDATE ONLY: no current.sif/module publication or acceptance-cache hit', flush=True)


if __name__ == '__main__':
    main()
