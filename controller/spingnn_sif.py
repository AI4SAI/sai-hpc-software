"""Managed overlay build, final-SIF science and verified native export gates."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile

import spingnn_release as release
from cp2k_image_driver import wrap_command
from delivery_layout import artifact_path, CONTRACT_SCHEMA
from export_native import export_delivery, verify_export
from remote_controller import container_command, project_batch_directives, project_qos


def preflight():
    settings = release.site()
    image = release.plain(settings['base_sif'])
    release.require(release.checksum(image) == settings['base_sha256'], 'SpinGNN base SIF identity changed')
    return image


def container(directory, identity, phase):
    settings = release.site()
    final = phase == 'verify'
    image = directory / 'result.sif' if final else Path(settings['base_sif'])
    binds = [(directory / 'input', '/input')]
    if phase == 'build':
        binds += [(Path(settings['export_python']).parent.parent, Path(settings['export_python']).parent.parent),
                  (directory / 'input', '/workspace/recipe/input')]
    payload = ['/usr/bin/bash', '/control/spingnn_sif_entry.sh', phase, identity['install_prefix']]
    command = container_command(image, payload, control=release.CONTROL,
        overlay=None if final else directory / 'work.ext3', jobs=2, gpu=bool(identity['gpus']),
        extra_binds=binds, runtime=directory / 'runtime')
    command[2:2] = ['--ignore-fakeroot-command', '--bind', str(directory / 'results') + ':/results:rw']
    if phase == 'build':
        for local, inside in (('evidence', '/workspace/recipe/evidence'), ('fixture', '/workspace/recipe/acceptance')):
            command[2:2] = ['--bind', str(directory / local) + ':' + inside + ':rw']
    for name in ('SLURM_JOB_ID', 'SLURM_JOB_PARTITION', 'SLURM_CPUS_PER_TASK', 'CUDA_VISIBLE_DEVICES'):
        if name in os.environ:
            command[2:2] = ['--env', name + '=' + os.environ[name]]
    command[2:2] = ['--env', 'SAI_SPINGNN_CONTEXT=' + ('container' if final else phase)]
    return wrap_command(release.PROJECT, directory, release.CONTROL, command)


def render_job(run_id, value):
    identity = release.validate_delivery(value, run_id)['identity']
    directory = release.task(run_id)
    profile = release.hardware(identity['target'])
    qos = project_qos(release.PROJECT, identity['target']) if identity['gpus'] else profile['qos']
    resources = ['#SBATCH --gres=gpu:1'] if identity['gpus'] else ['#SBATCH --cpus-per-task=8', '#SBATCH --mem=16G']
    return '\n'.join(['#!/bin/bash', '#SBATCH --job-name=spingnn2-sif-' + run_id,
        '#SBATCH --partition=' + identity['partition'], '#SBATCH --qos=' + qos,
        '#SBATCH --nodes=1', '#SBATCH --ntasks=1', *resources, '#SBATCH --time=180',
        *project_batch_directives(release.PROJECT, directory), '#SBATCH --export=NIL',
        '#SBATCH --output=' + str(directory / 'evidence/slurm-%j.log'),
        'set -eo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""',
        # NIL drops the account identity needed by site OpenMPI on the host.
        # Restore the submitter's real home, as in the existing beta jobs;
        # temporary files still use this run's private runtime directory.
        'export HOME=' + shlex.quote(str(Path.home())),
        'export USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?} SLURM_EXPORT_ENV=ALL',
        'source /etc/profile.d/lmod.sh', 'module purge', 'module use /opt/modules/modulefiles/devtools',
        'module load apptainer/1.4.4', 'set -u', 'umask 022',
        'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
        'export PYTHONDONTWRITEBYTECODE=1',
        'export TMPDIR=' + shlex.quote(str(directory / 'runtime')),
        'export APPTAINER_TMPDIR="$TMPDIR" APPTAINER_CACHEDIR=' + shlex.quote(str(directory / 'apptainer-cache')),
        shlex.join(['/usr/bin/python3', str(release.CONTROL / 'spingnn_sif.py'), 'run', run_id]), ''])


def inspect_request(directory):
    request = release.read(directory / 'request.json')
    identity = release.validate_delivery(request['delivery'], directory.name)['identity']
    release.inspect_submission(directory, dict(delivery=request['delivery'], controller=str(release.CONTROL)))
    release.require(release.checksum(directory / 'input/spingnn-dependencies/sources.json') == request['sources_sha256'], 'changed input manifest')
    return request, identity


def stage(directory, name, action):
    """Only a recorded successful stage is reused; incomplete bytes stay visible."""
    checkpoint = directory / ('stage-' + name + '.json')
    request_sha = release.checksum(directory / 'request.json')
    if checkpoint.exists():
        record = release.read(checkpoint)
        release.require(record['request_sha256'] == request_sha, 'SIF stage belongs to another request')
        for path, digest in record['outputs'].items():
            release.require(release.checksum(release.plain(directory / path)) == digest, 'changed completed SIF stage')
        return
    paths = action()
    release.save(checkpoint, dict(request_sha256=request_sha,
        outputs={str(path.relative_to(directory)): release.checksum(path) for path in paths}))


def run(run_id):
    directory = release.task(run_id)
    request, identity = inspect_request(directory)
    release.require(os.environ.get('SLURM_JOB_ID') == request['job_id'] and
                    os.environ.get('SLURM_JOB_PARTITION') == identity['partition'], 'wrong SIF build allocation')
    preflight()
    for part in ('fixture', 'results', 'evidence', 'runtime', 'apptainer-cache'):
        (directory / part).mkdir(exist_ok=True)
    allocation = subprocess.check_output(['scontrol', 'show', 'job', '-o', request['job_id']], text=True)
    release.immutable(directory / 'evidence/allocation.txt', allocation.encode())
    release.save(directory / 'evidence/host-context.json', dict(job=request['job_id'],
        partition=identity['partition'], uid=os.getuid(), affinity=sorted(os.sched_getaffinity(0)),
        mount_namespace=os.readlink('/proc/self/ns/mnt'), user_namespace=os.readlink('/proc/self/ns/user'),
        environment={name: value for name, value in os.environ.items() if name.startswith('SLURM_') or name == 'CUDA_VISIBLE_DEVICES'}))

    def build():
        overlay = directory / 'work.ext3'
        release.require(not overlay.exists(), 'unfinished overlay retained; use a new explicit release retry')
        subprocess.run(['apptainer', 'overlay', 'create', '--fakeroot', '--sparse', '--size',
                        str(release.site()['overlay_mb']), str(overlay)], check=True)
        overlay.chmod(0o600)
        subprocess.run(container(directory, identity, 'build'), check=True)
        return [directory / 'results/overlay-sync-build.json', overlay]
    # Export mutates the overlay. Once the immutable SIF is recorded, resume
    # from that image; before then the completed build overlay must still hash.
    if not (directory / 'stage-package.json').exists():
        stage(directory, 'build', build)

    def package():
        subprocess.run(container(directory, identity, 'export'), check=True)
        command = container_command(preflight(), ['/usr/bin/cat', '/workspace/final.squashfs'],
                                     overlay=str(directory / 'work.ext3') + ':ro', jobs=2)
        command = wrap_command(release.PROJECT, directory, release.CONTROL, command)
        squash = directory / 'final.squashfs'
        with squash.open('xb') as output:
            subprocess.run(command, stdout=output, check=True)
        image = directory / 'result.sif'
        release.require(not image.exists(), 'unrecorded SIF retained; explicit recovery required')
        subprocess.run(['apptainer', 'sif', 'new', str(image)], check=True)
        subprocess.run(['apptainer', 'sif', 'add', '--datatype', '4', '--partfs', '1', '--parttype', '2',
                        '--partarch', '2', '--groupid', '1', str(image), str(squash)], check=True)
        image.chmod(0o444)
        return [image]
    stage(directory, 'package', package)

    def accept_image():
        subprocess.run(container(directory, identity, 'verify'), check=True)
        return [directory / 'results/container/acceptance.json', directory / 'results/container/module-unload.txt']
    stage(directory, 'container-science', accept_image)
    image = directory / 'result.sif'
    digest = release.checksum(image)
    exported = export_delivery(image, digest, directory / 'native', [identity])

    def accept_native():
        subprocess.run(['bash', str(release.CONTROL / 'spingnn_accept.sh'), str(directory / 'native'),
            str(directory / 'results/exported-native'), str(release.CONTROL), str(directory / 'input/site.sh'),
            'exported-native'], check=True)
        return [directory / 'results/exported-native/acceptance.json', directory / 'results/exported-native/module-unload.txt']
    stage(directory, 'native-science', accept_native)
    verify_export(directory / 'native', exported['receipt'], digest, [identity])
    science = verify_science(directory, identity, request['job_id'])
    from spingnn_install import bundle
    installation = bundle(directory, identity, exported, release.CONTROL, release.FILES)
    release.save(directory / 'completion.json', dict(schema=2, request_sha256=release.checksum(directory / 'request.json'),
        identity=identity, sif_sha256=digest, base_sif=release.site()['base_sif'],
        base_sha256=release.site()['base_sha256'], export=exported, science=science, installer=installation))


def verify_science(directory, identity, job):
    result = {}
    host = release.read(directory / 'evidence/host-context.json')
    for context in ('container', 'exported-native'):
        case = directory / 'results' / context
        runtime = identity['install_prefix'] if context == 'container' else directory / 'native'
        value = release.science(case, identity, directory / 'native', job, runtime_prefix=runtime)
        release.require(value.get('execution_context') == context, 'wrong SpinGNN scientific execution context')
        if context == 'container':
            release.require(value.get('container') and value['mount_namespace'] != host['mount_namespace'],
                            'final SIF science did not enter a container namespace')
        else:
            release.require(not value.get('container') and value['mount_namespace'] == host['mount_namespace'],
                            'native export was not tested directly on the allocated host')
        release.require((case / 'module-unload.txt').read_text() == 'passed\n', 'module unload failed')
        result[context] = {str(path.relative_to(directory)): release.checksum(path)
                           for path in case.rglob('*') if path.is_file()}
    return result


def verified(run_id):
    directory = release.task(run_id)
    request, identity = inspect_request(directory)
    release.accounting(request['job_id'], directory, partition=identity['partition'])
    receipt = release.read(directory / 'completion.json')
    release.require(receipt['schema'] == 2 and receipt['identity'] == identity and
                    receipt['request_sha256'] == release.checksum(directory / 'request.json'), 'changed SIF completion identity')
    release.require(release.checksum(directory / 'result.sif') == receipt['sif_sha256'], 'changed accepted SIF')
    verify_export(directory / 'native', receipt['export']['receipt'], receipt['sif_sha256'], [identity])
    release.require(verify_science(directory, identity, request['job_id']) == receipt['science'], 'changed scientific evidence')
    for name, digest in receipt['installer'].items():
        release.require(release.checksum(directory / name) == digest, 'changed administrator delivery')
    return request, receipt


def publish(run_id):
    release.check_access()
    request, receipt = verified(run_id)
    identity = receipt['identity']
    artifact = artifact_path(release.ROOT, identity, run_id)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    directory = release.task(run_id)
    with (release.ROOT / '.sif-release.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not artifact.exists():
            os.link(directory / 'result.sif', artifact)
        release.require(release.checksum(artifact) == receipt['sif_sha256'], 'conflicting SIF publication')
        proof = dict(schema=1, contract_schema=CONTRACT_SCHEMA, identity=identity,
            software='spingnn2', version=identity['source_version'], source_sha=identity['source_sha'],
            track='release', source_ref=identity['source_ref'], target=identity['target'],
            recipe_sha256=identity['recipe_sha256'], artifact=str(artifact), sha256=receipt['sif_sha256'],
            build_verified=True, scientific_acceptance=True, native_export=receipt['export']['directory'],
            installer=str(directory / 'install.sh'), bundle=str(directory / 'payload.tar.gz'),
            completion_sha256=release.checksum(directory / 'completion.json'), created_ns=request['created_ns'],
            published=True, publication_scope='project-staging; administrator installation is explicit')
        release.save(artifact.with_suffix('.json'), proof)
        current = release.ROOT / 'release-targets' / identity['target'] / 'current.json'
        current.parent.mkdir(parents=True, exist_ok=True)
        if not current.exists() or release.read(current)['created_ns'] <= proof['created_ns']:
            release.atomic_text(current, json.dumps(proof, sort_keys=True) + '\n')
    return proof


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('run', 'publish'))
    parser.add_argument('run_id')
    args = parser.parse_args()
    result = run(args.run_id) if args.operation == 'run' else publish(args.run_id)
    print(json.dumps(result, sort_keys=True))
