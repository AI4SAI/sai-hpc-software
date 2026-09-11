#!/usr/bin/env python3
"""Runner-side source transport and isolated experimental candidate execution."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shlex
import subprocess
from md_tracking import REPOSITORIES, fingerprint
from remote_controller import safe_name, safe_sha
from source_cache import pack


def run(argv, **kwargs):
    return subprocess.run(list(map(str, argv)), check=True, text=True, **kwargs)


def main():
    request = json.loads(os.environ['MD_PAIR'])
    from md_controller import validate_pair
    validate_pair(request)
    user = safe_name(os.environ['REMOTE_USER'])
    sha = safe_sha(os.environ['GITHUB_SHA'])
    run_id = safe_name('md-' + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT'] + '-' + request['target'])
    temporary = Path(os.environ['RUNNER_TEMP'])
    control = Path(__file__).resolve().parent
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
    files = [*control.glob('md_*.py'), *control.glob('md_*.sh')]
    files += [control / name for name in ('remote_controller.py', 'source_cache.py', 'resolve_source.py', 'create_rootfs.sh')]
    for path in files:
        upload(path, snapshot + '/' + path.name)
    for name, source in request['sources'].items():
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
    pair_file = temporary / 'pair.json'
    pair_file.write_text(json.dumps(request, sort_keys=True) + '\n')
    upload(pair_file, remote_task + '/input/pair.json')
    ssh(['test', '-s', project + '/containers/base/minimal-v1.sif'])
    (results / 'request.json').write_text(json.dumps(dict(request, recipe_sha256=fingerprint(control))) + '\n')
    try:
        python('md_controller.py', 'submit', run_id, remote_task + '/input/pair.json')
        python('md_controller.py', 'monitor', run_id)
        science_run = safe_name(run_id + '-science')
        python('md_acceptance_controller.py', 'submit', science_run, run_id)
        python('md_acceptance_controller.py', 'monitor', science_run)
        run(['scp', '-q', *options, '-P', '12022', remote + ':' + remote_task + '/artifact.path', results / 'artifact.path'])
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
