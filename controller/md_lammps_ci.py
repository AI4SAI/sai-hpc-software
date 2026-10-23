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


def main():
    from lammps_acceptance import REFERENCE_SHA256, digest
    source = selected_source()
    delivery = make_delivery(source, os.environ['LAMMPS_TARGET'])
    run_id = safe_name('lmp-' + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT'] +
                       '-' + source['track'] + '-' + delivery['identity']['target'] + '-' +
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
        run(['scp', '-q', *options, '-P', '12022', *sources, remote + ':' + destination], timeout=1800)

    def python(name, *args, **kwargs):
        return ssh(['env', 'SAI_SOFTWARE_ROOT=' + project,
                    'python3', snapshot + '/' + name, *args], **kwargs)

    reference = CONTROL.parent / 'deployment/lammps-30Sep2026/native-reference.json'
    if digest(reference) != REFERENCE_SHA256:
        raise ValueError('the committed accepted LAMMPS reference changed')
    request = results / 'delivery.json'
    request.write_text(json.dumps(delivery, sort_keys=True) + '\n')
    ssh(['mkdir', '-p', snapshot])
    files = [p for p in CONTROL.glob('md_*.*') if p.suffix in ('.py', '.sh', '.json')]
    files += [CONTROL / name for name in ('remote_controller.py', 'source_cache.py', 'resolve_source.py',
              'create_rootfs.sh', 'release_contract.py', 'native_module.py', 'export_native.py',
              'module_publication.py', 'delivery_layout.py', 'cp2k_image_driver.py',
              'lammps_acceptance.py', 'lammps_rank.sh', 'lammps_verify.sbatch')]
    upload([*files, runtime_plan(), reference, request], snapshot + '/')
    decision = json.loads(python('release_contract.py', root, run_id, json.dumps([delivery['identity']]),
                                 *(['--retry'] if retry == 'true' else []), capture_output=True).stdout)
    (results / 'build-attempt.json').write_text(json.dumps(decision, sort_keys=True) + '\n')
    if not decision['build']:
        print('UNCHANGED_RELEASE_SKIPPED: existing attempt; no new scientific acceptance claimed', flush=True)
        return
    if decision['identities'] != [delivery['identity']]:
        raise ValueError('claimed LAMMPS source differs from the requested delivery')
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
    science = root + '/runtime-tests/' + run_id + '-science'
    try:
        python('md_lammps_standalone.py', 'submit', run_id, snapshot + '/delivery.json')
        python('md_lammps_standalone.py', 'monitor', run_id)
        python('lammps_acceptance.py', 'submit', root + '/runs/' + run_id,
               '--run-id', run_id + '-science', '--reference', snapshot + '/native-reference.json',
               '--delivery', snapshot + '/delivery.json')
        python('lammps_acceptance.py', 'monitor', science)
        python('lammps_acceptance.py', 'verify', science)
    finally:
        subprocess.run(['scp', '-q', *options, '-P', '12022', '-r',
                        remote + ':' + root + '/runs/' + run_id + '/results/.', str(results)], check=False)
        science_results = results / 'science'
        science_results.mkdir(exist_ok=True)
        # Scratch lives in PROJECT/runtime, outside this complete evidence tree.
        subprocess.run(['scp', '-q', *options, '-P', '12022', '-r',
                        remote + ':' + science + '/.', str(science_results)], check=False)
    print('LAMMPS_GPU_SCIENCE_ACCEPTED: native installation and publication remain separate', flush=True)


if __name__ == '__main__':
    main()
