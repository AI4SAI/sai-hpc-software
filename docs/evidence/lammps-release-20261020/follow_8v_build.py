#!/usr/bin/env python3
"""Follow the submitted 8V100V0 build, then use the repaired acceptance snapshot."""
from datetime import datetime, timezone
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

PROJECT = Path('/home/stardust/sai-hpc-software')
ROOT = PROJECT / 'experimental/deepmd-lammps'
DIAG = PROJECT / 'diagnostics/lammps-release-20261020'
BUILD = 'md-39427644750-1-2026-10-20-1dfd6f53c113abda'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('acceptance_controller', type=Path)
    parser.add_argument('acceptance_recipe')
    parser.add_argument('acceptance_helper', type=Path)
    args = parser.parse_args()
    acceptance, recipe, helper = args.acceptance_controller, args.acceptance_recipe, args.acceptance_helper
    if (acceptance.resolve() != acceptance or not acceptance.is_relative_to(ROOT / 'controller')
            or helper.resolve() != helper or not helper.is_relative_to(DIAG)):
        raise ValueError('untrusted acceptance handoff')
    os.umask(0o077)
    sys.dont_write_bytecode = True
    os.environ['SAI_SOFTWARE_ROOT'] = str(PROJECT)
    task = ROOT / 'runs' / BUILD
    request = json.loads((task / 'request.json').read_text())
    control = Path(request['controller'])
    stage_job = request['stage_jobs'].get('lammps')
    mount = request.get('mount_timeout_retry', {})
    extraction = request.get('artifact_extraction_recovery', {})
    known_stage = (stage_job == '1881546'
                   or mount.get('previous_job') == '1881546' and mount.get('job') == stage_job
                   or extraction.get('previous_job') == '1882104' and extraction.get('job') == stage_job
                   and extraction.get('no_recompilation') is True)
    if (control.resolve() != control or not control.is_relative_to(ROOT / 'controller')
            or request['stage_jobs'].get('deepmd') != '1881545'
            or not known_stage
            or request['delivery']['plan']['target'] != '8v100v0-avx512'):
        raise ValueError('unexpected 8V100V0 build')
    sys.path.insert(0, str(control))
    from md_tracking import fingerprint
    if fingerprint(control) != request['delivery']['recipe_sha256'] or fingerprint(acceptance) != recipe:
        raise ValueError('compiled or acceptance controller changed')
    record = dict(build_run=BUILD, workflow='39427644750', pid=os.getpid(), commands=[],
                  started_at=datetime.now(timezone.utc).isoformat(), build_controller=str(control),
                  build_recipe_sha256=request['delivery']['recipe_sha256'],
                  acceptance_controller=str(acceptance), acceptance_recipe_sha256=recipe,
                  helper_sha256=hashlib.sha256(helper.read_bytes()).hexdigest(),
                  follower_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    receipt = task / 'results' / ('follow-build-' + str(os.getpid()) + '.json')
    with (task / 'results/follow-build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

        def save():
            receipt.write_text(json.dumps(record, sort_keys=True) + '\n')

        def call(command):
            event = dict(argv=command, started_at=datetime.now(timezone.utc).isoformat())
            record['commands'].append(event)
            save()
            print(json.dumps(event, sort_keys=True), flush=True)
            result = subprocess.run(command, pass_fds=(lock.fileno(),))
            event.update(returncode=result.returncode, finished_at=datetime.now(timezone.utc).isoformat())
            save()
            result.check_returncode()

        try:
            deepmd_status = json.loads((task / 'results/deepmd-status.json').read_text()) if (task / 'results/deepmd-status.json').exists() else {}
            if deepmd_status.get('state') == 'COMPLETED' and deepmd_status.get('job') == '1881545' and deepmd_status.get('exit_code') == '0:0':
                stages = ('lammps',)
            else:
                stages = ('deepmd', 'lammps')
            for stage in stages:
                call(['python3', '-u', str(control / 'md_controller.py'), 'monitor', BUILD,
                      '--stage', stage, '--timeout', '172800', '--interval', '30'])
            if hashlib.sha256(helper.read_bytes()).hexdigest() != record['helper_sha256']:
                raise ValueError('acceptance helper changed during the build')
            call(['python3', '-u', str(helper), '8V100V0', str(acceptance), recipe])
            record['state'] = 'published-and-reverified'
        except BaseException as error:
            record.update(state='stopped', error=repr(error))
            raise
        finally:
            record['finished_at'] = datetime.now(timezone.utc).isoformat()
            save()


if __name__ == '__main__':
    main()
