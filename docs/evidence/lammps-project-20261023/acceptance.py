#!/usr/bin/env python3
"""Run the accepted independent LAMMPS GPU matrix on the project account.

Numerical comparisons and tolerances are the unchanged October 22 verifier.
This adapter accepts a separately verified export recovery and moves only the
new allocation, controller snapshot, inputs and scratch to the project tree.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
import acceptance_base as base
import recover

PACKAGE = Path(__file__).resolve().parent
PROJECT, ROOT, LEGACY = recover.PROJECT, recover.ROOT, recover.LEGACY.parent.parent
ACCOUNT = recover.ACCOUNT
base.PACKAGE, base.PROJECT, base.ROOT = PACKAGE, PROJECT, ROOT
require, read, digest, write = base.require, base.read, base.digest, base.write


def build_status(build):
    require(build.parent == ROOT / 'runs', 'recovered build must be under project')
    status = recover.status(build)
    require(status == read(build / 'results/status.json'), 'recovered build status changed')
    return status


base.build_status = build_status
original_guard = base.guard


def guard(output):
    require(os.environ.get('SLURM_JOB_ACCOUNT') == ACCOUNT, 'wrong scientific billing account')
    return original_guard(output)


base.guard = guard


def submit(build, reference_file):
    status = build_status(build)
    partition = status['delivery']['identity']['partition']
    reference = read(reference_file)['partitions'][partition]
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = ROOT / 'runtime-tests' / (build.name + '-science-' + stamp)
    require(output.is_relative_to(PROJECT) and output.resolve() == output, 'acceptance escaped project')
    output.mkdir(parents=True)
    recover.private(output / 'runtime')
    pins = {}

    def copy(source, name, checksum):
        require(digest(source) == checksum, 'accepted reference changed: ' + str(source))
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o444)
        pins[name] = checksum

    for name in ('acceptance.py', 'acceptance_base.py', 'recover.py', 'rank.sh', 'verify.sbatch'):
        copy(PACKAGE / name, 'control/' + name, digest(PACKAGE / name))
    for name, checksum in reference['tools'].items():
        copy(LEGACY / name, 'helpers/' + Path(name).name, checksum)
    for name, checksum in reference['case_files'].items():
        copy(LEGACY / reference['science_case'] / name, 'inputs/science/' + name, checksum)
    for name, case in reference['gpu'].items():
        for filename in ('data.lmp', 'in.lmp'):
            source = case['source'] + '/' + filename
            copy(LEGACY / source, 'inputs/gpu/' + name + '/' + filename, reference['files'][source])
    write(output / 'reference.json', reference)
    pins['reference.json'] = digest(output / 'reference.json')
    write(output / 'request.json', dict(schema=1, build=str(build), build_status=status,
          build_request_sha256=digest(build / 'request.json'), files=pins,
          reference_sha256=digest(reference_file)))
    (output / 'request.sha256').write_text(digest(output / 'request.json') + '\n')
    base.check_files(output, pins)
    command = ['sbatch', '--hold', '--parsable', '--partition=' + partition,
        '--qos=' + ('flood-1o2gpu' if partition == '16V100' else 'rush-1o2gpu'),
        '--account=' + ACCOUNT, '--chdir=' + str(output),
        '--job-name=lmp-independent-science-' + partition,
        '--output=' + str(output / 'slurm-%j.log'), str(output / 'control/verify.sbatch'), str(output)]
    subprocess.run(['bash', '-n', str(output / 'control/verify.sbatch')], check=True)
    subprocess.run(['sbatch', '--test-only', *command[3:]], cwd=output, check=True)
    job = subprocess.check_output(command, cwd=output, text=True).strip()
    require(re.fullmatch(r'[1-9][0-9]*', job), 'unexpected Slurm job handle')
    (output / 'job.id').write_text(job + '\n')
    subprocess.run(['scontrol', 'release', job], check=True)
    print(json.dumps(dict(job=job, partition=partition, account=ACCOUNT, output=str(output))), flush=True)


def verify(output):
    row = recover.accounting((output / 'job.id').read_text().strip())
    require(row[4:] == [ACCOUNT, str(output)], 'acceptance account/workdir changed')
    base.verify(output)


if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('submit', 'run', 'guard', 'inventory', 'verify'))
    parser.add_argument('directory', type=Path)
    parser.add_argument('--reference', type=Path)
    args = parser.parse_args()
    if args.operation == 'submit':
        require(args.reference is not None, 'accepted native-reference.json is required')
        submit(args.directory.resolve(), args.reference.resolve())
    elif args.operation == 'guard':
        print('\n'.join(guard(args.directory)))
    elif args.operation == 'verify':
        verify(args.directory)
    else:
        getattr(base, args.operation)(args.directory)
