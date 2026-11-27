#!/usr/bin/env python3
"""Daily relay transport for the SpinGNN native release controller."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import uuid

from ci import install_controller, stage_archives
from relay_checkpoint import Checkpoint, Pending, entry, remote as remote_call
from remote_controller import safe_name, safe_sha
from source_cache import checksum
from spingnn_inputs import prepare
from spingnn_release import CONTROL, FILES, ROOT, bootstrap, delivery


def main():
    source = json.loads(os.environ['SPINGNN_SOURCE'])
    commit = safe_sha(os.environ['GITHUB_SHA'])
    run_id = safe_name('spingnn2-' + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT']
                       + '-' + source['target'] + '-' + source['source_sha'][:12])
    value = delivery(source, run_id)
    relay = Checkpoint()
    relay.bind(dict(source=source, controller=commit, delivery=value))
    temporary = Path(os.environ['RUNNER_TEMP'])
    results = temporary / 'results'
    results.mkdir(exist_ok=True)
    remote = safe_name(os.environ['REMOTE_USER']) + '@c0.sai.ai-4s.com'
    options = ['-i', os.environ.get('SAI_RELAY_SSH_KEY', str(temporary / 'ssh/key')),
               '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'UserKnownHostsFile=' + str(CONTROL.parent / '.ci/slurm/known_hosts'),
               '-o', 'ConnectTimeout=20', '-o', 'ServerAliveInterval=20', '-o', 'ServerAliveCountMax=6']
    snapshot = ROOT / 'controller' / commit / run_id
    directory = ROOT / 'runs' / run_id

    def ssh(argv, **kwargs):
        response = subprocess.run(['ssh', *options, '-p', '12022', remote,
            shlex.join(list(map(str, argv)))], check=False, text=True, **kwargs)
        if response.returncode == 75:
            raise Pending('SpinGNN first-install/Slurm evidence pending')
        response.check_returncode()
        return response

    def upload(path, destination):
        paths = list(path) if isinstance(path, (tuple, list)) else [path]
        subprocess.run(['scp', '-q', *options, '-P', '12022', *map(str, paths),
                        remote + ':' + str(destination)], check=True, timeout=1800)

    def python(name, *args, **kwargs):
        return ssh(['env', 'PYTHONDONTWRITEBYTECODE=1', '/usr/bin/python3', snapshot / name, *args], **kwargs)

    if not relay.data.get('controller_ready'):
        ssh(['mkdir', '-p', snapshot])
        staging = str(snapshot) + '.upload-' + uuid.uuid4().hex
        ssh(['mkdir', staging])
        upload([CONTROL / name for name in FILES], staging + '/')
        remote_call(ssh, install_controller, staging, str(snapshot),
                    {name: checksum(CONTROL / name) for name in FILES}, [])
        relay.set('controller_ready', True)
    payload = json.dumps(value, sort_keys=True)
    retry = os.environ.get('RETRY_RELEASES', 'false')
    if retry not in ('true', 'false'):
        raise ValueError('retry_releases must be an explicit boolean')
    claim = json.loads(python('spingnn_release.py', 'claim', run_id, payload,
                      *(['--retry'] if retry == 'true' else []), capture_output=True).stdout)
    (results / 'build-attempt.json').write_text(json.dumps(claim, indent=2) + '\n')
    if not claim['build']:
        print('SPINGNN_RELEASE_ALREADY_ATTEMPTED', json.dumps(claim['skipped']), flush=True)
        return
    if bootstrap(value['identity']) is not None:
        result = json.loads(python('spingnn_release.py', 'adopt', run_id, payload, capture_output=True).stdout)
    else:
        if not relay.data.get('inputs_ready'):
            archives = prepare(temporary / 'spingnn-inputs', source)
            stage_archives(python, upload, ROOT, directory / 'input', archives)
            relay.set('inputs_ready', True)
        relay.submitted(ssh, str(directory), dict(delivery=value, controller=str(snapshot)),
                        lambda: python('spingnn_release.py', 'submit', run_id, payload))
        relay.wait(ssh, str(directory))
        result = json.loads(python('spingnn_release.py', 'publish', run_id, capture_output=True).stdout)
    if result.get('published') is not True:
        raise ValueError('SpinGNN release did not pass publication gates')
    (results / 'publication.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    entry(main)
