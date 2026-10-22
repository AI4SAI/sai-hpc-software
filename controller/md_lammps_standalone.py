#!/usr/bin/env python3
"""Build a LAMMPS candidate against a pinned, installed DeePMD dependency.

Only LAMMPS is compiled and packaged. Build verification is deliberately
distinct from scientific acceptance and administrator installation in /opt.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import time

from cp2k_image_driver import wrap_command
from md_controller import PROJECT, ROOT, task
from md_tracking import TARGETS, fingerprint
from release_contract import make_identity, validate_identity
from remote_controller import container_command, safe_name, site_app_binds
from source_cache import checksum

CONTROL = Path(__file__).resolve().parent


def runtime_plan(control=CONTROL):
    path = Path(control) / 'runtime-plan.json'
    return path if path.is_file() else Path(control).parent / 'deployment/lammps-30Sep2026/runtime-plan.json'


def dependency(target, control=CONTROL):
    if target not in TARGETS:
        raise ValueError('unsupported standalone LAMMPS target')
    plan = runtime_plan(control)
    rows = [item for item in json.loads(plan.read_text())['installations']
            if item['identity']['software'] == 'deepmd-kit' and item['identity']['target'] == target]
    if len(rows) != 1:
        raise ValueError('exactly one accepted site DeePMD dependency is required')
    spec = rows[0]
    identity = validate_identity(spec['identity'])
    if spec['prefix'] != identity['install_prefix']:
        raise ValueError('installed dependency path differs from its release identity')
    return dict(identity=identity, runtime_plan_sha256=checksum(plan)), spec['files']


def verify_dependency(dep, records, root=Path('/')):
    """Read all installed dependency bytes/links; never write its prefix."""
    prefix = Path(root) / dep['identity']['install_prefix'].lstrip('/')
    if prefix.resolve() != prefix:
        raise ValueError('installed DeePMD prefix has been redirected')
    expected = {row['path']: row for row in records}
    if len(expected) != len(records) or set(expected) != {'.', *(str(p.relative_to(prefix)) for p in prefix.rglob('*'))}:
        raise ValueError('installed DeePMD file set differs from the accepted runtime')
    for name, row in expected.items():
        path = prefix / name
        info = path.lstat()
        kind = ('symlink' if stat.S_ISLNK(info.st_mode) else 'directory' if stat.S_ISDIR(info.st_mode)
                else 'file' if stat.S_ISREG(info.st_mode) else 'special')
        if kind != row['kind']:
            raise ValueError('installed DeePMD file type changed: ' + name)
        if kind == 'symlink':
            if os.readlink(path) != row['target']:
                raise ValueError('installed DeePMD symlink changed: ' + name)
        elif stat.S_IMODE(info.st_mode) != row['mode']:
            raise ValueError('installed DeePMD permissions changed: ' + name)
        elif kind == 'file' and (info.st_size != row['size'] or checksum(path) != row['sha256']):
            raise ValueError('installed DeePMD content changed: ' + name)
    return dict(prefix=str(prefix), paths=len(records), verified=True)


def make_delivery(source, target, control=CONTROL):
    dep, _ = dependency(target, control)
    recipe = hashlib.sha256((fingerprint(control) + '\n' + dep['runtime_plan_sha256']).encode()).hexdigest()
    identity = make_identity('lammps', source['track'], source['source_ref'], source['source_sha'],
                             source['source_version'], recipe, target)
    return dict(schema=1, identity=identity, deepmd_dependency=dep)


def validate_delivery(delivery, control=CONTROL):
    identity = validate_identity(delivery['identity'])
    expected = make_delivery(identity, identity['target'], control)
    if delivery != expected:
        raise ValueError('standalone delivery differs from the source, recipe or installed dependency')
    return delivery


def render(delivery, run_id):
    delivery = validate_delivery(delivery)
    identity = delivery['identity']
    directory = task(run_id)
    base = PROJECT / 'containers/base/minimal-v1.sif'
    image = directory / 'candidate.sif'
    overlay = directory / 'work.ext3'
    dep = delivery['deepmd_dependency']['identity']['install_prefix']
    binds = [*site_app_binds('lammps'), (dep, dep)]
    sources = [(ROOT / 'cache/repositories' / name, '/input/' + name) for name in ('lammps', 'deepmd-kit')]
    payload = json.dumps(delivery, sort_keys=True, separators=(',', ':'))

    def command(phase):
        final = phase == 'verify'
        argv = container_command(image if final else base,
            ['/usr/bin/bash', '/control/md_lammps_standalone_entry.sh', phase, payload],
            overlay=None if final else overlay, control=CONTROL, jobs=24, gpu=True,
            runtime=directory / 'runtime' if final else None,
            extra_binds=[*binds, *([] if final else sources)])
        return ([arg for arg in argv if arg != '--fakeroot'] if final
                else wrap_command(ROOT, directory, CONTROL, argv))

    emit = wrap_command(ROOT, directory, CONTROL, container_command(
        base, ['/usr/bin/cat', '/workspace/final.squashfs'], overlay=str(overlay) + ':ro', jobs=24))
    lines = ['#!/usr/bin/env bash', '#SBATCH --job-name=lammps-' + safe_name(run_id),
             '#SBATCH --partition=' + identity['partition'], '#SBATCH --qos=rush-gpu',
             '#SBATCH --nodes=1', '#SBATCH --ntasks=1', '#SBATCH --gpus-per-node=4', '#SBATCH --time=330',
             '#SBATCH --output=' + str(directory / 'results/slurm-%j.log'), '#SBATCH --export=NIL',
             'set -eo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""',
             'source /etc/profile.d/lmod.sh', 'module load apptainer/1.4.4', 'set -u', 'umask 077',
             'export TMPDIR=' + shlex.quote(str(directory / 'runtime')),
             'export APPTAINER_TMPDIR="$TMPDIR"',
             'export APPTAINER_CACHEDIR=' + shlex.quote(str(directory / 'apptainer-cache')),
             'export APPTAINERENV_SAI_MD_ALLOCATED_JOB="$SLURM_JOB_ID"',
             'export APPTAINERENV_SAI_MD_ALLOCATED_NODE="$(hostname)"',
             'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
             '[[ ${SLURM_CPUS_ON_NODE:?} -ge 24 ]]',
             shlex.join(['apptainer', 'overlay', 'create', '--fakeroot', '--sparse', '--size', '32768', str(overlay)]),
             shlex.join(command('build')), shlex.join(command('export')),
             shlex.join(emit) + ' > ' + shlex.quote(str(directory / 'final.squashfs')),
             shlex.join(['apptainer', 'sif', 'new', str(image)]),
             shlex.join(['apptainer', 'sif', 'add', '--datatype', '4', '--partfs', '1', '--parttype', '2',
                         '--partarch', '2', '--groupid', '1', str(image), str(directory / 'final.squashfs')]),
             shlex.join(command('verify')), shlex.join(['chmod', '0444', str(image)]),
             'echo LAMMPS_STANDALONE_BUILD_VERIFIED_NOT_PUBLISHED']
    return '\n'.join(lines) + '\n'


def submit(run_id, delivery):
    delivery = validate_delivery(delivery)
    directory = task(run_id)
    if directory.exists():
        raise ValueError('standalone run already exists; inspect its job instead of resubmitting')
    dep, files = dependency(delivery['identity']['target'])
    observed = verify_dependency(dep, files)
    for name, sha in (('lammps', delivery['identity']['source_sha']),
                      ('deepmd-kit', dep['identity']['source_sha'])):
        subprocess.run(['git', '--git-dir', str(ROOT / 'cache/repositories' / name),
                        'cat-file', '-e', sha + '^{commit}'], check=True)
    for name in ('results', 'runtime', 'apptainer-cache'):
        (directory / name).mkdir(parents=True)
    script = directory / 'job.sbatch'
    script.write_text(render(delivery, run_id))
    subprocess.run(['bash', '-n', str(script)], check=True)
    subprocess.run(['sbatch', '--test-only', str(script)], check=True)
    record = dict(delivery=delivery, controller=str(CONTROL), job_script_sha256=checksum(script),
                  dependency_verification=observed)
    (directory / 'request.json').write_text(json.dumps(record, sort_keys=True) + '\n')
    job = subprocess.check_output(['sbatch', '--parsable', str(script)], text=True).strip().split(';')[0]
    if not job.isdigit():
        raise ValueError('invalid standalone Slurm job handle')
    (directory / 'job.id').write_text(job + '\n')
    print(json.dumps(dict(run_id=run_id, job=job, partition=delivery['identity']['partition'])), flush=True)


def status(run_id):
    directory = task(run_id)
    record = json.loads((directory / 'request.json').read_text())
    delivery = validate_delivery(record['delivery'])
    if record['controller'] != str(CONTROL) or checksum(directory / 'job.sbatch') != record['job_script_sha256']:
        raise ValueError('standalone build provenance changed')
    job = (directory / 'job.id').read_text().strip()
    if not job.isdigit():
        raise ValueError('invalid standalone Slurm job handle')
    active = subprocess.check_output(['squeue', '-h', '-j', job, '-o', '%T|%R'], text=True).strip()
    if active:
        print(job + ': ' + active, flush=True)
        return 3
    accounting = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', job,
                                          '-o', 'JobIDRaw,State,ExitCode'], text=True)
    row = next((line.split('|') for line in accounting.splitlines() if line.split('|')[0] == job), None)
    if not row or row[1] in ('RUNNING', 'PENDING', 'COMPLETING'):
        return 3
    result = dict(job=job, state=row[1], exit_code=row[2], delivery=delivery,
                  build_verified=False, scientific_verified=False, native_runtime_verified=False, published=False)
    if row[1:3] == ['COMPLETED', '0:0']:
        log = (directory / f'results/slurm-{job}.log').read_text()
        if 'LAMMPS_STANDALONE_BUILD_VERIFIED_NOT_PUBLISHED' not in log:
            raise ValueError('successful accounting without a verified standalone SIF')
        dep, files = dependency(delivery['identity']['target'])
        verify_dependency(dep, files)
        image = directory / 'candidate.sif'
        result.update(build_verified=True, artifact=str(image), artifact_sha256=checksum(image))
    (directory / 'results/status.json').write_text(json.dumps(result, sort_keys=True) + '\n')
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result['build_verified'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='op', required=True)
    plan = sub.add_parser('plan')
    plan.add_argument('--target', choices=TARGETS, required=True)
    plan.add_argument('--track', choices=('development', 'release', 'prerelease'), default='development')
    launch = sub.add_parser('submit')
    launch.add_argument('run_id'); launch.add_argument('request')
    for name in ('status', 'monitor'):
        sub.add_parser(name).add_argument('run_id')
    args = parser.parse_args()
    if args.op == 'plan':
        from release_contract import resolve_tracks
        source = resolve_tracks('lammps', [args.track])[0]
        if source['status'] != 'resolved':
            raise ValueError('selected LAMMPS channel has no resolved source')
        print(json.dumps(make_delivery(source, args.target), sort_keys=True))
    elif args.op == 'submit':
        submit(args.run_id, json.loads(Path(args.request).read_text()))
    elif args.op == 'status':
        return status(args.run_id)
    else:
        deadline = time.monotonic() + 21600
        while time.monotonic() < deadline:
            outcome = status(args.run_id)
            if outcome != 3:
                return outcome
            time.sleep(30)
        raise TimeoutError('observation deadline; recorded Slurm job remains available for monitoring')


if __name__ == '__main__':
    raise SystemExit(main())
