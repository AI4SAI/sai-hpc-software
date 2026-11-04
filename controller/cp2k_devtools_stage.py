#!/usr/bin/env python3
"""Submit independent final-prefix library builds; never install host /opt."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess

from cp2k_dependency_stage import SEED_SHA256, paths, read_json, accounting
from cp2k_devtools import TARGETS as PARTITIONS, validate_provider
from cp2k_image_driver import wrap_command, validate_receipt as validate_driver, module_load_command
from devtools_install import require, checksum
from remote_controller import (TARGETS, container_command, project_batch_directives,
                               project_qos, private_scratch, safe_name)

PURPOSE = 'independent-spack-devtools-not-cp2k-acceptance'
TOOLCHAINS = ('gcc', 'cmake', 'nvidia', 'openmpi', 'saiblas', 'fftw', 'libxc', 'spglib', 'elpa')


def library_container(image, command, **kwargs):
    args = container_command(image, command, **kwargs)
    index = args.index('/opt/devtools:/opt/devtools:ro')
    require(args[index - 1] == '--bind', 'Unexpected container policy')
    # New library prefixes live in the overlay/SIF, not beneath a host bind.
    args[index - 1:index + 1] = [item for name in TOOLCHAINS for item in
                               ('--bind', f'/opt/devtools/{name}:/opt/devtools/{name}:ro')]
    return args


def recipe_files(controller):
    # Record the complete trusted controller snapshot, including all imports.
    return {p.name: checksum(p) for p in sorted(Path(controller).iterdir())
            if p.is_file() and p.suffix in ('.py', '.sh', '.f90', '.cmake', '.json')}


def render(project, controller, run, partition, *, minutes=None):
    project, controller, run = paths(project, controller, run)
    target_name = PARTITIONS[partition]
    target = TARGETS[target_name]
    gpus = 2 if partition == '8V100V0' else target['gpus']
    jobs = 12 if gpus == 2 else target.get('build_jobs', 8)
    minutes = (240 if gpus else 720) if minutes is None else minutes
    require(1 <= minutes <= (240 if gpus else 2880), 'Invalid time limit')
    image = project / 'containers/base/minimal-v1.sif'
    overlay, sif = run / 'work.ext3', run / 'libraries.sif'
    seed = project / 'cache/spack/bootstrap/spack-source-v2.ext3'
    def container(phase):
        args = library_container(sif if phase == 'verify' else image,
            ['/usr/bin/bash', '/control/cp2k_devtools_entry.sh', phase, partition],
            overlay=None if phase == 'verify' else overlay, control=controller,
            jobs=jobs, gpu=bool(gpus), runtime=run / 'runtime' if phase == 'verify' else None,
            extra_binds=() if phase == 'verify' else (
                (project / 'cache/spack', '/input/spack'), ('/var/lib/dpkg', '/var/lib/dpkg'),
                ('/etc/os-release', '/etc/os-release'), ('/etc/alternatives', '/etc/alternatives'), ('/usr/bin', '/bin')))
        args[2:2] = ['--bind', str(run / 'results') + ':/results:rw']
        if phase == 'verify':
            args[2:2] = ['--bind', str(run / 'runtime') + ':/tmp:rw',
                         '--bind', str(run / 'runtime') + ':/var/tmp:rw']
            return args
        return wrap_command(project, run, controller, args)
    emit = wrap_command(project, run, controller, library_container(
        image, ['/usr/bin/cat', '/workspace/final.squashfs'], overlay=str(overlay) + ':ro', jobs=jobs))
    q = shlex.quote
    lines = ['#!/bin/bash', '#SBATCH --job-name=sai-devtools-' + partition,
             '#SBATCH --partition=' + partition, '#SBATCH --qos=' + project_qos(project, target_name),
             '#SBATCH --nodes=1', '#SBATCH --ntasks=1', '#SBATCH --export=NIL',
             '#SBATCH --time=' + str(minutes), '#SBATCH --output=' + str(run / 'results/slurm-%j.log'),
             *project_batch_directives(project, run)]
    lines += (['#SBATCH --gpus-per-node=' + str(gpus)] if gpus else
              ['#SBATCH --cpus-per-task=' + str(jobs), '#SBATCH --mem=28G'])
    lines += ['set -eo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""',
              'source /etc/profile.d/lmod.sh', module_load_command(), 'set -u', 'umask 077',
              'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
              'export TMPDIR=' + q(str(run / 'runtime')), 'export APPTAINER_TMPDIR="$TMPDIR"',
              'export APPTAINER_CACHEDIR=' + q(str(run / 'apptainer-cache')),
              'test ! -e ' + q(str(overlay)),
              shlex.join(['cp', '--sparse=always', '--reflink=auto', '--', str(seed), str(overlay)]),
              shlex.join(['chmod', '600', str(overlay)]),
              shlex.join(['printf', '%s  %s\n', SEED_SHA256, str(overlay)]) + ' | sha256sum --check --status',
              shlex.join(['/usr/sbin/e2fsck', '-pf', str(overlay)]) + ' || test "$?" -eq 1',
              shlex.join(['/usr/sbin/resize2fs', str(overlay), '16384M']),
              shlex.join(container('build')),
              shlex.join(['/usr/sbin/e2fsck', '-fn', str(overlay)]) + ' > ' + q(str(run / 'results/fsck.log')) + ' 2>&1',
              shlex.join(emit) + ' > ' + q(str(run / 'libraries.squashfs')),
              shlex.join(['apptainer', 'sif', 'new', str(sif)]),
              shlex.join(['apptainer', 'sif', 'add', '--datatype', '4', '--partfs', '1', '--parttype', '2',
                          '--partarch', '2', '--groupid', '1', str(sif), str(run / 'libraries.squashfs')]),
              shlex.join(container('verify')),
              shlex.join(['sha256sum', str(sif)]) + ' > ' + q(str(run / 'results/artifact.sha256')),
              shlex.join(['chmod', '444', str(sif)]),
              'echo DEVTOOLS_SIF_CONSUMERS_VERIFIED_NOT_CP2K_ACCEPTANCE']
    return '\n'.join(lines) + '\n'


def submit(project, controller, run_id, partition):
    project, controller, run = paths(project, controller, Path(project) / 'runs' / safe_name(run_id))
    require(not run.exists(), 'Run already exists; use a fresh identity')
    script = render(project, controller, run, partition)
    run.mkdir()
    (run / 'results').mkdir()
    for name in ('runtime', 'apptainer-cache'):
        private_scratch(run / name)
    (run / 'job.sbatch').write_text(script)
    subprocess.run(['bash', '-n', str(run / 'job.sbatch')], check=True)
    request = dict(schema=1, purpose=PURPOSE, partition=partition, controller=str(controller),
                   script_sha256=checksum(run / 'job.sbatch'), controller_files=recipe_files(controller))
    (run / 'request.json').write_text(json.dumps(request, sort_keys=True) + '\n')
    result = subprocess.run(['sbatch', '--hold', '--parsable', str(run / 'job.sbatch')],
                            cwd=run, capture_output=True, text=True, check=True)
    job = result.stdout.strip().split(';')[0]
    require(job.isdigit(), 'Invalid scheduler response')
    (run / 'job.id').write_text(job + '\n')
    request['job_id'] = job
    (run / 'request.json').write_text(json.dumps(request, sort_keys=True) + '\n')
    subprocess.run(['scontrol', 'release', job], check=True)
    print(job, flush=True)


def accepted(project, run_id):
    run = Path(project) / 'runs' / safe_name(run_id)
    request = read_json(run / 'request.json')
    _, controller, run = paths(project, request['controller'], run)
    require(request['purpose'] == PURPOSE and request['controller_files'] == recipe_files(controller),
            'Controller changed after submission')
    require(request['script_sha256'] == checksum(run / 'job.sbatch'), 'Submitted script changed')
    job, status = accounting(run)
    require(job == request['job_id'] and status == ['COMPLETED', '0:0'], 'Build is not successfully completed')
    results = run / 'results'
    validate_driver(project, read_json(results / 'image-driver.json'))
    require(read_json(results / 'overlay-sync-dependencies.json')['synchronized'] is True, 'Overlay not synchronized')
    require((results / 'fsck.log').is_file(), 'Missing filesystem verification')
    lifecycle = sorted(results.glob('managed-overlay-*.json'))
    require(lifecycle, 'Missing managed writable-overlay lifecycle receipt')
    for path in lifecycle:
        record = read_json(path)
        require(record.get('completed') is True and record.get('accepted') is True
                and record.get('forced_stop') is False and record.get('payload_returncode') == 0
                and record.get('unmount_returncode') == 0 and record.get('driver_returncode') == 0,
                'Writable-overlay teardown was not clean')
    for name in ('driver-cleanup-failure-*', 'managed-overlay-failure-*'):
        require(not list(results.glob(name)), 'Container teardown failed')
    provider = read_json(results / 'provider.json')
    validate_provider(provider)
    require(provider['install']['partition'] == request['partition'], 'Partition changed')
    smoke = read_json(results / 'sif-consumer/passed.json')
    require(smoke['contract_sha256'] == provider['contract_sha256'], 'SIF consumer identity mismatch')
    require(checksum(run / 'libraries.sif') == (results / 'artifact.sha256').read_text().split()[0], 'SIF changed')
    return provider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='op', required=True)
    create = sub.add_parser('submit')
    for field in ('project', 'controller', 'run_id'):
        create.add_argument(field)
    create.add_argument('partition', choices=sorted(PARTITIONS))
    check = sub.add_parser('check')
    check.add_argument('project'); check.add_argument('run_id')
    args = parser.parse_args()
    if args.op == 'submit':
        submit(args.project, args.controller, args.run_id, args.partition)
    else:
        provider = accepted(args.project, args.run_id)
        print(json.dumps({'partition': provider['install']['partition'], 'contract_sha256': provider['contract_sha256'],
                          'library_consumers_verified': True, 'cp2k_scientific_verified': False}))


if __name__ == '__main__':
    main()
