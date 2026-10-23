#!/usr/bin/env python3
"""Recover only the export of a completed LAMMPS compile, preserving its recipe.

The failed source run stays failed and untouched. A distinct project allocation
exports a checksum-verified overlay copy using a byte-identical frozen recipe.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

PROJECT = Path('/org/org-open-sai/proj-sai-hpc-software')
ROOT = PROJECT / 'experimental/deepmd-lammps'
LEGACY = Path('/home/stardust/sai-hpc-software/experimental/deepmd-lammps')
ACCOUNT = 'proj-sai-hpc-software'
SOURCE_SHA = 'a0e2bd6efb3d67a29c0c4e4de3b19556ffdb9d5b'
DRIVER_SHA = '46c1a8c8f42ab2419ea88072e41d89903c453d93f52485b5baddea5ca238bb10'
DRIVER_NAME = 'fuse2fs-' + DRIVER_SHA + '/bin/fuse2fs'


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(8 * 1024 * 1024):
            value.update(block)
    return value.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')


def canonical(path):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path and not path.is_symlink(), 'redirected path')
    return path


def private(path):
    canonical(path).mkdir(parents=True, exist_ok=True)
    for name in os.listxattr(path):
        if name in ('system.posix_acl_access', 'system.posix_acl_default'):
            os.removexattr(path, name)
    path.chmod(0o700)


def accounting(job):
    require(re.fullmatch(r'[1-9][0-9]*', job), 'invalid job identifier')
    text = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', job,
        '-o', 'JobIDRaw,State,ExitCode,Partition,Account,WorkDir'], text=True)
    rows = [line.split('|') for line in text.splitlines() if line.split('|')[0] == job]
    require(len(rows) == 1, 'missing or ambiguous Slurm accounting')
    return rows[0][:6]


def files(control):
    require(not any(p.is_symlink() for p in control.iterdir()), 'symlink in frozen controller')
    return {p.name: digest(p) for p in sorted(control.iterdir()) if p.is_file()}


def validate_recipe(control, delivery):
    code = '''import json,sys
sys.path.insert(0, sys.argv[1])
import md_lammps_standalone as old
delivery = old.validate_delivery(json.loads(sys.argv[2]))
dep, records = old.dependency(delivery['identity']['target'])
old.verify_dependency(dep, records)
print(json.dumps(delivery, sort_keys=True))
'''
    observed = json.loads(subprocess.check_output(['/usr/bin/python3', '-B', '-c', code,
        str(control), json.dumps(delivery)], text=True,
        env=dict(os.environ, SAI_SOFTWARE_ROOT=str(PROJECT), PYTHONDONTWRITEBYTECODE='1')))
    require(observed == delivery, 'original delivery changed')


def prepare(source, run):
    source, run = canonical(source), canonical(run)
    require(source.parent == LEGACY / 'runs' and run.parent == ROOT / 'runs', 'invalid run roots')
    require(not run.exists(), 'recovery run already exists')
    record = read(source / 'request.json')
    job = (source / 'job.id').read_text().strip()
    row = accounting(job)
    require(row[1:4] == ['FAILED', '255:0', '8V100V0'], 'unexpected source failure')
    identity = record['delivery']['identity']
    require(identity['source_sha'] == SOURCE_SHA and identity['target'] == '8v100v0-avx512',
            'unexpected source or target')
    log = source / ('results/slurm-' + job + '.log')
    require('MD_NATIVE_BUILD_FEATURE_PARITY_PASSED' in log.read_text() and
            'fuse2fs failed to mount' in log.read_text(), 'compile did not finish before export mount failure')
    require(digest(source / 'job.sbatch') == record['job_script_sha256'], 'source script changed')
    control = canonical(record['controller'])
    require(control.is_relative_to(LEGACY / 'controller'), 'unexpected frozen controller')
    validate_recipe(control, record['delivery'])
    run.mkdir(parents=True)
    (run / 'results').mkdir()
    for name in ('runtime', 'apptainer-cache'):
        private(run / name)
    original = ROOT / 'controller/lammps-recovery-20261023' / run.name / 'original'
    original.mkdir(parents=True)
    pins = files(control)
    for name in pins:
        shutil.copyfile(control / name, original / name)
        (original / name).chmod(0o444)
    require(files(original) == pins, 'frozen controller copy differs')
    driver = ROOT / 'cache/tools' / DRIVER_NAME
    if not driver.exists():
        driver.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(LEGACY / 'cache/tools' / DRIVER_NAME, driver)
        driver.chmod(0o555)
    require(not driver.is_symlink() and digest(driver) == DRIVER_SHA and
            not driver.stat().st_mode & 0o222, 'project overlay driver differs')
    print('Hashing completed source overlay', flush=True)
    original_sha = digest(source / 'work.ext3')
    print('Copying completed overlay into project', flush=True)
    subprocess.run(['cp', '--reflink=never', '--sparse=always', '--',
                    str(source / 'work.ext3'), str(run / 'work.ext3')], check=True)
    (run / 'work.ext3').chmod(0o600)
    require(digest(run / 'work.ext3') == original_sha, 'overlay copy checksum mismatch')
    request = dict(schema=1, operation='recover-standalone-export', source=str(source), source_job=job,
        source_accounting=row, source_overlay_sha256=original_sha,
        source_files={name: digest(source / name) for name in
            ('request.json', 'job.id', 'job.sbatch', 'results/status.json', 'results/slurm-' + job + '.log')},
        delivery=record['delivery'], controller=str(original), controller_files=pins,
        copied_overlay_sha256=original_sha, base_sha256=digest(PROJECT / 'containers/base/minimal-v1.sif'),
        driver_sha256=DRIVER_SHA, recovery_helper_sha256=digest(__file__))
    write(run / 'request.json', request)
    (run / 'request.sha256').write_text(digest(run / 'request.json') + '\n')
    print(json.dumps(dict(prepared=str(run), source_overlay_sha256=original_sha)), flush=True)


def context(run):
    run = canonical(run)
    require(run.parent == ROOT / 'runs', 'recovery escaped project')
    require(digest(run / 'request.json') == (run / 'request.sha256').read_text().strip(),
            'recovery request changed')
    record = read(run / 'request.json')
    require(record['schema'] == 1 and record['operation'] == 'recover-standalone-export', 'wrong recovery')
    source = canonical(record['source'])
    require(source.parent == LEGACY / 'runs', 'source escaped recorded legacy tree')
    for name, checksum in record['source_files'].items():
        require(digest(source / name) == checksum, 'failed source evidence changed: ' + name)
    require(accounting(record['source_job'])[1:4] == ['FAILED', '255:0', '8V100V0'],
            'failed source job was relabelled')
    require(files(canonical(record['controller'])) == record['controller_files'], 'frozen controller changed')
    require(digest(PROJECT / 'containers/base/minimal-v1.sif') == record['base_sha256'], 'base image changed')
    validate_recipe(record['controller'], record['delivery'])
    return record


def render(run):
    record = context(run)
    control = Path(record['controller'])
    sys.path.insert(0, str(control))
    from remote_controller import container_command, site_app_binds
    from cp2k_image_driver import wrap_command
    image, overlay = run / 'candidate.sif', run / 'work.ext3'
    payload = json.dumps(record['delivery'], sort_keys=True, separators=(',', ':'))
    dep = record['delivery']['deepmd_dependency']['identity']['install_prefix']
    binds = [*site_app_binds('lammps'), (dep, dep)]

    def command(phase):
        final = phase == 'verify'
        argv = container_command(image if final else PROJECT / 'containers/base/minimal-v1.sif',
            ['/usr/bin/bash', '/control/md_lammps_standalone_entry.sh', phase, payload],
            overlay=None if final else overlay, control=control, jobs=6, gpu=True,
            runtime=run / 'runtime' if final else None, extra_binds=binds)
        return ([arg for arg in argv if arg != '--fakeroot'] if final else
                wrap_command(ROOT, run, control, argv))

    emit = wrap_command(ROOT, run, control, container_command(PROJECT / 'containers/base/minimal-v1.sif',
        ['/usr/bin/cat', '/workspace/final.squashfs'], overlay=str(overlay) + ':ro', jobs=6))
    lines = ['#!/usr/bin/env bash', '#SBATCH --job-name=lmp-export-recovery-8V100V0',
        '#SBATCH --partition=8V100V0', '#SBATCH --qos=rush-1o2gpu', '#SBATCH --account=' + ACCOUNT,
        '#SBATCH --chdir=' + str(run), '#SBATCH --nodes=1', '#SBATCH --ntasks=1',
        '#SBATCH --gpus-per-node=1', '#SBATCH --time=30', '#SBATCH --export=NIL',
        '#SBATCH --output=' + str(run / 'results/slurm-%j.log'), 'set -eo pipefail',
        'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD="" PYTHONDONTWRITEBYTECODE=1',
        'source /etc/profile.d/lmod.sh', 'module load apptainer/1.4.4', 'set -u', 'umask 077',
        'export TMPDIR=' + shlex.quote(str(run / 'runtime')), 'export APPTAINER_TMPDIR="$TMPDIR"',
        'export APPTAINER_CACHEDIR=' + shlex.quote(str(run / 'apptainer-cache')),
        'export APPTAINERENV_SAI_MD_ALLOCATED_JOB="$SLURM_JOB_ID"',
        'export APPTAINERENV_SAI_MD_ALLOCATED_NODE="$(hostname)"',
        'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
        shlex.join(command('export')), shlex.join(emit) + ' > ' + shlex.quote(str(run / 'final.squashfs')),
        shlex.join(['apptainer', 'sif', 'new', str(image)]),
        shlex.join(['apptainer', 'sif', 'add', '--datatype', '4', '--partfs', '1', '--parttype', '2',
                    '--partarch', '2', '--groupid', '1', str(image), str(run / 'final.squashfs')]),
        shlex.join(command('verify')), shlex.join(['chmod', '0444', str(image)]),
        'echo LAMMPS_STANDALONE_EXPORT_RECOVERY_VERIFIED_NOT_PUBLISHED']
    return '\n'.join(lines) + '\n'


def submit(run):
    context(run)
    require(not (run / 'job.id').exists(), 'recovery already submitted')
    script = run / 'job.sbatch'
    with script.open('x') as stream:
        stream.write(render(run))
    subprocess.run(['bash', '-n', str(script)], check=True)
    subprocess.run(['sbatch', '--test-only', str(script)], cwd=run, check=True)
    write(run / 'submission.json', dict(script_sha256=digest(script)))
    job = subprocess.check_output(['sbatch', '--hold', '--parsable', str(script)], cwd=run, text=True).strip()
    require(re.fullmatch(r'[1-9][0-9]*', job), 'invalid recovery job')
    (run / 'job.id').write_text(job + '\n')
    subprocess.run(['scontrol', 'release', job], check=True)
    print(json.dumps(dict(job=job, run=str(run), account=ACCOUNT)), flush=True)


def status(run, verify_source=False):
    record = context(run)
    job = (run / 'job.id').read_text().strip()
    row = accounting(job)
    require(row[1:6] == ['COMPLETED', '0:0', '8V100V0', ACCOUNT, str(run)], 'recovery not completed: ' + str(row))
    require(digest(run / 'job.sbatch') == read(run / 'submission.json')['script_sha256'], 'recovery script changed')
    log = (run / ('results/slurm-' + job + '.log')).read_text()
    require('LAMMPS_STANDALONE_SIF_INVENTORY_VERIFIED' in log and
            'LAMMPS_STANDALONE_EXPORT_RECOVERY_VERIFIED_NOT_PUBLISHED' in log, 'missing export verification')
    if verify_source:
        require(digest(Path(record['source']) / 'work.ext3') == record['source_overlay_sha256'],
                'original completed overlay changed')
    return dict(schema=1, operation=record['operation'], job=job, state=row[1], exit_code=row[2],
        account=row[4], workdir=row[5], source_job=record['source_job'], source_job_state='FAILED',
        source_overlay_sha256=record['source_overlay_sha256'], delivery=record['delivery'],
        build_verified=True, scientific_verified=False, native_runtime_verified=False, published=False,
        artifact=str(run / 'candidate.sif'), artifact_sha256=digest(run / 'candidate.sif'),
        request_sha256=digest(run / 'request.json'))


if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare', 'submit', 'status'))
    parser.add_argument('run', type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--verify-source', action='store_true')
    args = parser.parse_args()
    if args.operation == 'prepare':
        require(args.source is not None, 'source run required')
        prepare(args.source, args.run)
    elif args.operation == 'submit':
        submit(args.run)
    else:
        result = status(args.run, args.verify_source)
        output = args.run / 'results/status.json'
        if not output.exists():
            write(output, result)
        require(read(output) == result, 'recovery result changed')
        print(json.dumps(result, sort_keys=True), flush=True)
