#!/usr/bin/env python3
"""Verify the production preparation helper on a new home-backed 256 MiB image."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import cp2k_overlay_workdir as helper
import repro

BASE = Path('/home/stardust/sai-hpc-software/diagnostics/cp2k-release-20261016/overlay-workdir-fix-20261017-b')
DRIVER_SHA = '46c1a8c8f42ab2419ea88072e41d89903c453d93f52485b5baddea5ca238bb10'


def main():
    if Path(__file__).resolve() != BASE / 'input/fix-proof.py':
        raise RuntimeError('fixed diagnostic deployment path required')
    for name in ('runtime', 'cache', 'results', 'driver-bin'):
        (BASE / name).mkdir()
    env = dict(os.environ, TMPDIR=str(BASE / 'runtime'), APPTAINER_TMPDIR=str(BASE / 'runtime'),
               APPTAINER_CACHEDIR=str(BASE / 'cache'), PYTHONDONTWRITEBYTECODE='1')
    source, target = BASE / 'source.ext3', BASE / 'application.ext3'
    subprocess.run([str(repro.APPTAINER), 'overlay', 'create', '--sparse', '--size', '256',
                    '--fakeroot', str(source)], env=env, check=True)
    payload = BASE / 'payload'
    payload.write_text('upper payload must remain byte-identical\n')
    empty = BASE / 'empty'
    empty.touch()
    commands = ['mkdir /upper/workspace', 'mkdir /upper/workspace/rm-probe',
                'write ' + str(payload) + ' /upper/payload', 'mkdir /work/work']
    for i in range(1, 4097):
        commands += ['mkdir /work/work/' + str(i),
                     'write ' + str(empty) + ' /work/work/' + str(i) + '/.wh..wh..opq']
    commands += ['write ' + str(payload) + ' /work/work/.fuse_hidden0000000100000002']
    with (BASE / 'seed.log').open('w') as log:
        subprocess.run([helper.DEBUGFS, '-w', '-f', '-', str(source)],
                       input='\n'.join(commands) + '\n', text=True, check=True,
                       stdout=log, stderr=subprocess.STDOUT)
    source.chmod(0o400)
    expected = repro.digest(source)
    receipt = helper.prepare_clone(source, target, expected, BASE / 'results')
    assert receipt['status'] == 'prepared'
    assert len(receipt['removed_entries']) == 8193
    assert repro.digest(source) == expected
    output = subprocess.run([helper.DEBUGFS, '-R', 'cat /upper/payload', str(target)],
                            capture_output=True, text=True, check=True)
    assert output.stdout == payload.read_text()
    driver = repro.PROJECT / ('cache/tools/fuse2fs-' + DRIVER_SHA + '/bin/fuse2fs')
    assert repro.digest(driver) == DRIVER_SHA
    config = BASE / 'apptainer.conf'
    config.write_text(repro.CONFIG.read_text() + '\nbinary path = ' + str(driver.parent) +
                      ':$PATH:/usr/bin:/bin:/usr/sbin:/sbin\n')
    argv = [str(repro.APPTAINER), '--config', str(config), 'exec', '--fakeroot',
            '--cleanenv', '--containall', '--no-home', '--no-mount',
            'bind-paths,home,cwd,tmp,hostfs', '--pwd', '/', '--net', '--network', 'none']
    for path in ('/usr', '/sbin', '/lib', '/lib64', '/opt/devtools'):
        argv += ['--bind', path + ':' + path + ':ro']
    argv += ['--bind', str(BASE / 'input') + ':/probe:ro',
             '--bind', str(BASE / 'results') + ':/results:rw',
             '--env', 'PYTHONDONTWRITEBYTECODE=1,TMPDIR=/workspace',
             '--overlay', str(target), str(repro.PROJECT / 'containers/base/minimal-v1.sif'),
             '/usr/bin/python3', '-B', '/probe/repro.py', '--workload']
    with (BASE / 'workload.log').open('w') as log:
        subprocess.run(argv, env=env, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=480)
    workload = json.loads((BASE / 'results/workload.json').read_text())
    assert workload['failures'] == [] and workload['last_iteration'] == 8191
    output = subprocess.run([helper.DEBUGFS, '-R', 'cat /upper/payload', str(target)],
                            capture_output=True, text=True, check=True)
    assert output.stdout == payload.read_text()
    assert repro.digest(source) == expected
    with (BASE / 'final-fsck.log').open('w') as log:
        subprocess.run([helper.FSCK, '-fn', str(target)], check=True,
                       stdout=log, stderr=subprocess.STDOUT)
    result = dict(status='passed', job_id=os.environ.get('SLURM_JOB_ID'),
                  source_sha256=expected, source_unchanged=True,
                  prepared_sha256=receipt['prepared_overlay_sha256'],
                  final_application_sha256=repro.digest(target), driver_sha256=DRIVER_SHA,
                  helper_sha256=repro.digest(Path(helper.__file__)),
                  removed_entries=len(receipt['removed_entries']),
                  cleanup_commands=len(receipt['cleanup_commands']), workload=workload,
                  upper_payload_unchanged=True, final_fsck='passed', historical_images_used=False)
    (BASE / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
