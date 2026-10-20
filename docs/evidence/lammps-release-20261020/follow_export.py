#!/usr/bin/env python3
"""Follow the two scoped release exports through the existing acceptance gates."""
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
RECIPE = '296ed3baa11b0bb5629dfb60e7bf396083367a613719dfd8d21767082fa98097'
RUNS = (
    'md-39403117741-1-2026-10-20-9bf6ae82814537d7-export',
    'md-39403084059-1-2026-10-20-5fef92e054bacdaf-export',
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_id', choices=RUNS)
    args = parser.parse_args()
    os.umask(0o077)
    sys.dont_write_bytecode = True
    os.environ['SAI_SOFTWARE_ROOT'] = str(PROJECT)
    directory = ROOT / 'runs' / args.run_id
    if directory.resolve() != directory:
        raise ValueError('unexpected export path')
    # This lock covers monitoring, submission and publication, including all
    # child processes; reconnecting must not launch another acceptance chain.
    with (directory / 'results/follow-export.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        request = json.loads((directory / 'request.json').read_text())
        control = Path(request['controller'])
        if (control.resolve() != control or not control.is_relative_to(ROOT / 'controller')
                or request['run_id'] != args.run_id
                or request['metadata_recovery']['compiled_recipe_sha256'] != RECIPE):
            raise ValueError('export recovery identity differs')
        sys.path.insert(0, str(control))
        from md_ci import inspect_existing_acceptance, run_acceptance_gate
        from md_controller import validate_pair
        from md_tracking import fingerprint
        from source_cache import checksum
        delivery = validate_pair(request['delivery'])
        if delivery['recipe_sha256'] != RECIPE or fingerprint(control) != RECIPE:
            raise ValueError('frozen compiled recipe changed')
        qos = ('rush-1o2gpu' if delivery['identities']['lammps']['partition'] == '4V100'
               else 'flood-1o2gpu')
        record = dict(run_id=args.run_id, controller=str(control), recipe_sha256=RECIPE,
                      helper_sha256=checksum(Path(__file__)), pid=os.getpid(),
                      started_at=datetime.now(timezone.utc).isoformat(),
                      scheduler_override={'SBATCH_QOS': qos,
                                          'SBATCH_EXPORT': 'ALL'}, commands=[])
        record['submission_environment'] = dict(
            PATH='/usr/bin:/bin', HOME=str(Path.home()), USER='stardust', LOGNAME='stardust',
            SAI_SOFTWARE_ROOT=str(PROJECT), PYTHONDONTWRITEBYTECODE='1',
            **record['scheduler_override'])
        receipt = directory / 'results' / ('follow-export-' + str(os.getpid()) + '.json')

        def save():
            receipt.write_text(json.dumps(record, sort_keys=True) + '\n')

        def remote_python(name, *argv):
            command = ['python3', '-u', str(control / name), *map(str, argv)]
            environment = dict(os.environ)
            # QoS affects queue priority only. Keep the frozen job scripts,
            # target, ranks, GPU counts and science/performance rules intact.
            if argv[0] == 'submit':
                # The frozen NIL script predates explicit restoration of the
                # real account home. ALL exports only this explicit minimal
                # environment and avoids Slurm's implicit login-env retrieval.
                environment = dict(record['submission_environment'])
            if argv[0] == 'monitor':
                command += ['--timeout', '172800', '--interval', '30']
            event = dict(argv=command, started_at=datetime.now(timezone.utc).isoformat())
            record['commands'].append(event)
            save()
            print(json.dumps(event, sort_keys=True), flush=True)
            result = subprocess.run(command, env=environment, pass_fds=(lock.fileno(),))
            event.update(returncode=result.returncode,
                         finished_at=datetime.now(timezone.utc).isoformat())
            save()
            result.check_returncode()

        def existing(stage):
            return inspect_existing_acceptance(ROOT, control, args.run_id, stage, delivery)

        try:
            status_file = directory / 'results/status.json'
            status = json.loads(status_file.read_text()) if status_file.exists() else {}
            if (status.get('build_verified') is True and
                    status.get('job') == (directory / 'job.id').read_text().strip()):
                from md_publication import build_record
                _, image, digest = build_record(args.run_id)
                record['revalidated_build'] = dict(artifact=str(image), artifact_sha256=digest)
                save()
            else:
                remote_python('md_controller.py', 'monitor', args.run_id)
            record['performance_run'] = run_acceptance_gate(remote_python, args.run_id, existing)
            record['state'] = 'published-and-reverified'
        except BaseException as error:
            record.update(state='stopped', error=repr(error))
            raise
        finally:
            record['finished_at'] = datetime.now(timezone.utc).isoformat()
            save()


if __name__ == '__main__':
    main()
