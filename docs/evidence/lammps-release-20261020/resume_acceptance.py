#!/usr/bin/env python3
"""Accept the scoped release SIFs with an explicitly pinned new controller."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

PROJECT = Path('/home/stardust/sai-hpc-software')
ROOT = PROJECT / 'experimental/deepmd-lammps'
BUILDS = {
    '16V100': 'md-39403084059-1-2026-10-20-5fef92e054bacdaf-export',
    '4V100': 'md-39403117741-1-2026-10-20-9bf6ae82814537d7-export',
    '8V100V0': 'md-39427644750-1-2026-10-20-1dfd6f53c113abda',
}
REASON = ('Restore baseline wheel backend plugin lookup and requested OpenMP threads; '
          'restore isolated MPI HOME; parse the one-step warmup separately; '
          'back Nsight container temporary files with dedicated home storage')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', choices=BUILDS)
    parser.add_argument('controller', type=Path)
    parser.add_argument('recipe')
    args = parser.parse_args()
    os.umask(0o077)
    sys.dont_write_bytecode = True
    os.environ['SAI_SOFTWARE_ROOT'] = str(PROJECT)
    control = args.controller
    if control.resolve() != control or not control.is_relative_to(ROOT / 'controller'):
        raise ValueError('untrusted acceptance controller')
    sys.path.insert(0, str(control))
    from md_tracking import fingerprint
    from md_acceptance_recovery import capture
    from md_publication import build_record
    from md_ci import run_performance_gate
    from source_cache import checksum
    import md_acceptance_controller as science
    import md_performance_controller as performance
    if fingerprint(control) != args.recipe:
        raise ValueError('acceptance snapshot changed')
    build_run = BUILDS[args.target]
    build = ROOT / 'runs' / build_run
    delivery = json.loads((build / 'request.json').read_text())['delivery']
    recovery = capture(ROOT, control, build_run, delivery, REASON)
    _, image, digest = build_record(build_run, controller_recovery=recovery)
    science_run = build_run + '-science-recovery-' + args.recipe[:12]
    env = dict(PATH='/usr/bin:/bin', HOME=str(Path.home()), USER='stardust', LOGNAME='stardust',
               SAI_SOFTWARE_ROOT=str(PROJECT), PYTHONDONTWRITEBYTECODE='1', SBATCH_EXPORT='ALL',
               SBATCH_QOS='flood-1o2gpu' if args.target == '16V100' else 'rush-1o2gpu')
    with (build / 'results/follow-export.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt = build / 'results' / ('acceptance-recovery-' + str(os.getpid()) + '.json')
        record = dict(build_run=build_run, science_run=science_run, controller_recovery=recovery,
                      artifact=str(image), artifact_sha256=digest, helper_sha256=checksum(Path(__file__)),
                      pid=os.getpid(), submission_environment=env, commands=[],
                      started_at=datetime.now(timezone.utc).isoformat())

        def save():
            receipt.write_text(json.dumps(record, sort_keys=True) + '\n')

        def call(name, *arguments):
            command = ['python3', '-u', str(control / name), *map(str, arguments)]
            if arguments[0] == 'monitor':
                command += ['--timeout', '172800', '--interval', '30']
            event = dict(argv=command, started_at=datetime.now(timezone.utc).isoformat())
            record['commands'].append(event)
            save()
            print(json.dumps(event, sort_keys=True), flush=True)
            result = subprocess.run(command, env=env, pass_fds=(lock.fileno(),))
            event.update(returncode=result.returncode, finished_at=datetime.now(timezone.utc).isoformat())
            save()
            result.check_returncode()

        def existing(stage):
            run = science_run if stage == 'science' else build_run + '-' + stage
            directory = ROOT / 'runtime-tests' / run
            if not directory.exists():
                return False
            if {path.name for path in directory.iterdir()} == {'recovery-attempts'}:
                return False  # An explicit setup-only recovery retained the failed attempt.
            request = json.loads((directory / 'request.json').read_text())
            module = science if stage == 'science' else performance
            expected = dict(run_id=run, build_run=build_run, delivery=delivery,
                            artifact=str(image), artifact_sha256=digest, controller_recovery=recovery)
            if stage == 'science':
                expected.update(nodes=2, ranks=4, acceptance_recipe_sha256=args.recipe)
            else:
                expected.update(science_run=science_run, recipe_sha256=args.recipe)
            if (any(request.get(key) != value for key, value in expected.items())
                    or request.get('job_script_sha256') != checksum(directory / 'job.sbatch')
                    or (directory / 'job.sbatch').read_text() != module.render(request, directory)):
                raise ValueError('existing recovery task differs from pinned request')
            job = (directory / 'job.id').read_text().strip()
            if not job.isdigit():
                raise ValueError('invalid recovery job handle')
            status_file = directory / 'results/status.json'
            if status_file.exists():
                status = json.loads(status_file.read_text())
                if status.get('state') == 'COMPLETED' and status.get('exit_code') == '0:0':
                    return 'completed'
                raise ValueError('previous recovery attempt failed; retain it for inspection')
            return 'pending'

        try:
            previous = existing('science')
            if not previous:
                call('md_acceptance_controller.py', 'submit', science_run, build_run,
                     '--nodes', '2', '--recover-controller', REASON)
            call('md_acceptance_controller.py', 'verify' if previous == 'completed' else 'monitor', science_run)
            record['performance_run'] = run_performance_gate(call, build_run, science_run, existing)
            call('md_publication.py', 'publish', build_run, science_run, record['performance_run'])
            call('md_publication.py', 'verify', build_run)
            record['state'] = 'published-and-reverified'
        except BaseException as error:
            record.update(state='stopped', error=repr(error))
            raise
        finally:
            record['finished_at'] = datetime.now(timezone.utc).isoformat()
            save()


if __name__ == '__main__':
    main()
