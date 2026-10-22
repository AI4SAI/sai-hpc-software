#!/usr/bin/env python3
"""Actions entry for LAMMPS alone, reusing accepted installed DeePMD libraries."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import subprocess

from md_lammps_standalone import CONTROL, make_delivery, runtime_plan
from release_contract import resolve_tracks
from remote_controller import safe_name, safe_sha
from source_cache import pack


def run(argv, **kwargs):
    return subprocess.run(list(map(str, argv)), check=True, text=True, **kwargs)


def main():
    source = resolve_tracks('lammps', [os.environ.get('LAMMPS_TRACK', 'development')])[0]
    if source['status'] != 'resolved':
        raise ValueError('selected LAMMPS source is unavailable')
    delivery = make_delivery(source, os.environ['LAMMPS_TARGET'])
    run_id = safe_name('lmp-' + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT'] +
                       '-' + delivery['identity']['target'] + '-' + datetime.now(timezone.utc).strftime('%Y%m%d'))
    user = safe_name(os.environ['REMOTE_USER'])
    commit = safe_sha(os.environ['GITHUB_SHA'])
    temporary = Path(os.environ['RUNNER_TEMP'])
    results = temporary / 'results'
    results.mkdir(exist_ok=True)
    project = '/home/' + user + '/sai-hpc-software'
    root = project + '/experimental/deepmd-lammps'
    snapshot = root + '/controller/' + commit + '/' + run_id
    remote = user + '@c0.sai.ai-4s.com'
    options = ['-i', str(temporary / 'ssh/key'), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'UserKnownHostsFile=' + str(CONTROL.parent / '.ci/slurm/known_hosts'),
               '-o', 'ConnectTimeout=20', '-o', 'ServerAliveInterval=20', '-o', 'ServerAliveCountMax=6']

    def ssh(argv, **kwargs):
        return run(['ssh', *options, '-p', '12022', remote, shlex.join(list(map(str, argv)))], **kwargs)

    def upload(path, destination):
        run(['scp', '-q', *options, '-P', '12022', path, remote + ':' + destination], timeout=1800)

    def python(name, *args, **kwargs):
        return ssh(['python3', snapshot + '/' + name, *args], **kwargs)

    ssh(['mkdir', '-p', snapshot])
    files = [p for p in CONTROL.glob('md_*.*') if p.suffix in ('.py', '.sh', '.json')]
    files += [CONTROL / name for name in ('remote_controller.py', 'source_cache.py', 'resolve_source.py',
              'create_rootfs.sh', 'release_contract.py', 'native_module.py', 'export_native.py',
              'module_publication.py', 'delivery_layout.py', 'cp2k_image_driver.py')]
    for path in files:
        upload(path, snapshot + '/' + path.name)
    upload(runtime_plan(), snapshot + '/runtime-plan.json')
    request = results / 'delivery.json'
    request.write_text(json.dumps(delivery, sort_keys=True) + '\n')
    upload(request, snapshot + '/delivery.json')
    for name, identity in (('lammps', delivery['identity']), ('deepmd-kit', delivery['deepmd_dependency']['identity'])):
        sha = identity['source_sha']
        cache = root + '/cache/repositories/' + name
        inventory = json.loads(python('source_cache.py', 'inventory', cache, '--commit', sha, capture_output=True).stdout)
        if sha in inventory['cache_shas']:
            print('SOURCE_CACHE_HIT', name, sha, flush=True)
            continue
        repository = temporary / (name + '.git')
        run(['git', 'clone', '--bare', 'https://github.com/' + identity['repository'] + '.git', repository])
        run(['git', '--git-dir', repository, 'fetch', '--no-tags', 'origin', sha])
        parts = temporary / ('parts-' + name)
        pack(repository, sha, parts)
        destination = snapshot + '/input/' + name
        ssh(['mkdir', '-p', destination])
        upload(parts / 'manifest.json', destination + '/manifest.json')
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda i: upload(parts / f'source.part.{i:02d}', destination + f'/source.part.{i:02d}'), range(8)))
        python('source_cache.py', 'receive', cache, destination)
    try:
        python('md_lammps_standalone.py', 'submit', run_id, snapshot + '/delivery.json')
        python('md_lammps_standalone.py', 'monitor', run_id)
    finally:
        subprocess.run(['scp', '-q', *options, '-P', '12022', '-r',
                        remote + ':' + root + '/runs/' + run_id + '/results/.', str(results)], check=False)
    print('LAMMPS_CANDIDATE_BUILT: scientific acceptance and native installation remain separate', flush=True)


if __name__ == '__main__':
    main()
