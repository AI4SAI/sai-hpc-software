#!/usr/bin/env python3
"""Manual Actions entry: pinned NEPAdapters release, SIF and native acceptance."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import time

from nepadapters_release import FILES, PARTITIONS, SOURCE_ARCHIVE_SHA256, SOURCE_SHA, VERSION
from nepadapters_native import FILES as NATIVE_FILES
from remote_controller import SAI_PROJECT_ROOT, safe_name, safe_sha
from source_cache import checksum


def main():
    # Compile once on AVX2, then test these exact plugin bytes on every target.
    build_partition = '16V100'
    user, commit = safe_name(os.environ['REMOTE_USER']), safe_sha(os.environ['GITHUB_SHA'])
    run_id = safe_name('nepa-' + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT'] + '-shared')
    temporary = Path(os.environ['RUNNER_TEMP'])
    control = Path(__file__).resolve().parent
    results = temporary / 'results'
    results.mkdir(exist_ok=True)
    root = str(SAI_PROJECT_ROOT / 'experimental/nepadapters')
    snapshot = root + '/controller/' + commit + '/' + run_id
    build = root + '/runs/' + run_id
    natives = {part: root + '/runtime-tests/' + run_id + '-' + part for part in PARTITIONS}
    remote = user + '@c0.sai.ai-4s.com'
    options = ['-i', str(temporary / 'ssh/key'), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'UserKnownHostsFile=' + str(control.parent / '.ci/slurm/known_hosts'),
               '-o', 'ConnectTimeout=20', '-o', 'ServerAliveInterval=20', '-o', 'ServerAliveCountMax=6']

    def ssh(argv, **kwargs):
        return subprocess.run(['ssh', *options, '-p', '12022', remote, shlex.join(list(map(str, argv)))],
                              text=True, **kwargs)

    def upload(paths, destination):
        subprocess.run(['scp', '-q', *options, '-P', '12022', *map(str, paths), remote + ':' + destination], check=True)

    def python(name, *args, **kwargs):
        return ssh(['python3', '-B', snapshot + '/' + name, *args], **kwargs)

    def poll(argv, label):
        deadline = time.monotonic() + 5 * 3600
        while time.monotonic() < deadline:
            result = ssh(argv, capture_output=True)
            if result.returncode == 0:
                print(label + ': accepted', flush=True)
                return json.loads(result.stdout)
            if result.returncode != 3:
                raise RuntimeError(label + ': ' + result.stdout + result.stderr)
            print(label + ': queued/running', flush=True)
            time.sleep(45)
        raise TimeoutError(label + ': Slurm job remains active; inspect the recorded handoff')

    # Recreate the exact release archive instead of trusting a mutable tag URL.
    source = temporary / 'nepadapters-source'
    subprocess.run(['git', 'init', str(source)], check=True)
    subprocess.run(['git', '-C', str(source), 'fetch', '--depth=1',
                    'https://github.com/MagTheoryLab/NEPAdapters.git', SOURCE_SHA], check=True)
    archive = temporary / 'nepadapters.tar.gz'
    subprocess.run(['git', '-C', str(source), 'archive', '--format=tar.gz', '-o', str(archive), 'FETCH_HEAD'], check=True)
    if checksum(archive) != SOURCE_ARCHIVE_SHA256:
        raise ValueError('pinned release archive checksum differs')
    ssh(['mkdir', '-p', snapshot], check=True)
    upload([control / f for f in sorted(set(FILES) | set(NATIVE_FILES))], snapshot + '/')
    upload([archive], snapshot + '/nepadapters.tar.gz')
    handoff = dict(version=VERSION, source_sha=SOURCE_SHA, layout='shared', controller_sha=commit,
                   build=build, native=natives, controller=snapshot)
    (results / 'submitted.json').write_text(json.dumps(handoff, indent=2) + '\n')
    try:
        python('nepadapters_release.py', 'submit', '--source', snapshot + '/nepadapters.tar.gz',
               '--run-id', run_id, '--partition', build_partition, check=True)
        handoff['sif'] = poll(['python3', '-B', snapshot + '/nepadapters_release.py', 'status', build], 'SIF')
        for part, native in natives.items():
            python('nepadapters_native.py', 'submit', build, native, '--partition', part, check=True)
        for part, native in natives.items():
            proof = poll(['python3', '-B', snapshot + '/nepadapters_native.py', 'verify', native], part)
            (results / ('native-' + part + '.json')).write_text(json.dumps(proof, indent=2) + '\n')
        (results / 'accepted-handoffs.json').write_text(json.dumps(
            [dict(build=build, native=native) for native in natives.values()], indent=2) + '\n')
        handoff['accepted'] = True
        (results / 'handoff.json').write_text(json.dumps(handoff, indent=2) + '\n')
    finally:
        for path, name in [(build + '/results/.', 'build'),
                           *((native + '/acceptance.json', 'native-' + part + '.json')
                             for part, native in natives.items())]:
            subprocess.run(['scp', '-q', *options, '-P', '12022', '-r', remote + ':' + path, str(results / name)], check=False)


if __name__ == '__main__':
    main()
