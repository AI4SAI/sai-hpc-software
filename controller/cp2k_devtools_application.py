#!/usr/bin/env python3
"""Test CP2K against a copied dependency SIF, without host installation/publication."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time

from cp2k_dependency_stage import paths, read_json
from cp2k_devtools import TARGETS as PARTITIONS
from cp2k_devtools_stage import library_container, recipe_files
from cp2k_image_driver import wrap_command, module_load_command
from devtools_install import checksum, require
from release_contract import make_identity, validate_identity
from remote_controller import (TARGETS, cp2k_mpi_build_binds, private_scratch,
                               project_batch_directives, project_qos, safe_name)

PURPOSE = 'cp2k-application-on-copied-devtools-sif-not-publication'


def write_json(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write('\n')


def copy_image(source, destination, expected):
    source, destination = Path(source), Path(destination)
    require(source.resolve() == source and source.is_file(), 'Invalid source SIF')
    require(destination.resolve() == destination and not destination.exists(), 'Copy already exists or redirected')
    before = source.stat()
    require(checksum(source) == expected, 'Source SIF checksum changed')
    with source.open('rb') as inp, destination.open('xb') as out:
        shutil.copyfileobj(inp, out, 8 * 1024 * 1024)
        out.flush()
        os.fsync(out.fileno())
    destination.chmod(0o400)
    require(not os.path.samestat(before, destination.stat()), 'SIF copy must have a separate inode')
    require(checksum(destination) == expected and checksum(source) == expected, 'SIF copy changed content')
    return dict(source=str(source), copy=str(destination), sha256=expected,
                source_inode=before.st_ino, copy_inode=destination.stat().st_ino,
                source_size=before.st_size, source_mtime_ns=before.st_mtime_ns)


def render(project, controller, run, identity, *, node=None, jobs=8, minutes=240, verify_only=False):
    project, controller, run = paths(project, controller, run)
    identity = validate_identity(identity)
    target_name, partition = identity['target'], identity['partition']
    target = TARGETS[target_name]
    require(identity['software'] == 'cp2k' and PARTITIONS[partition] == target_name, 'Wrong application identity')
    require(1 <= jobs <= 16 and 1 <= minutes <= 240, 'Invalid probe resource limit')
    if node:
        safe_name(node)
    overlay, image = run / 'work.ext3', run / 'dependencies.sif'
    extra = [(run / 'input/cp2k-updates', '/input/updates'),
             (project / 'cache/cp2k-dependencies', '/input/dependencies'),
             (project / 'cache/cp2k-probe', '/input/probe'),
             ('/var/lib/dpkg', '/var/lib/dpkg'), ('/etc/os-release', '/etc/os-release'),
             ('/etc/alternatives', '/etc/alternatives'), ('/usr/bin', '/bin'),
             *cp2k_mpi_build_binds()]
    def command(phase):
        if phase == 'build':
            payload = ['/usr/bin/env', 'SAI_BUILD_PARTITION=' + partition, '/usr/bin/bash',
                       '/control/cp2k_container_entry.sh', 'build', 'cp2k', identity['source_sha'],
                       identity['source_version'], target_name, json.dumps(identity, sort_keys=True)]
            binds, repository = extra, project / 'cache/repositories/cp2k'
        else:
            payload = ['/usr/bin/bash', '/control/cp2k_devtools_application_entry.sh',
                       target_name, identity['install_prefix'], identity['source_sha']]
            binds, repository = [(run / 'input/science', '/input/science')], None
        argv = library_container(image, payload, control=controller, repository=repository,
                                 overlay=str(overlay) + (':ro' if phase == 'verify' else ''),
                                 jobs=jobs, gpu=bool(target['gpus']), extra_binds=binds,
                                 runtime=run / 'runtime' if phase == 'verify' else None)
        # The managed outer process already supplies the root-mapped namespace.
        # Do not add libfakeroot's inode/type emulation to the inner SIF overlay.
        argv.insert(2, '--ignore-fakeroot-command')
        argv[2:2] = ['--bind', str(run / 'results') + ':/results:rw']
        if phase == 'verify':
            argv[2:2] = ['--bind', str(run / 'runtime') + ':/tmp:rw',
                         '--bind', str(run / 'runtime') + ':/var/tmp:rw']
        return wrap_command(project, run, controller, argv)
    helper = ['/usr/bin/python3', str(controller / Path(__file__).name)]
    lines = ['#!/bin/bash', '#SBATCH --job-name=cp2k-devtools-app-' + partition,
             '#SBATCH --partition=' + partition, '#SBATCH --qos=' + project_qos(project, target_name),
             '#SBATCH --nodes=1', '#SBATCH --ntasks=' + ('2' if target['gpus'] else '1'), '#SBATCH --export=NIL',
             '#SBATCH --time=' + str(minutes), '#SBATCH --output=' + str(run / 'results/slurm-%j.log'),
             *project_batch_directives(project, run)]
    if node:
        lines.append('#SBATCH --nodelist=' + node)
    # The two-rank cuSOLVERMp/NCCL checks require a distinct GPU per rank.
    # Match Slurm's task count so the site mapping also selects one rank/GPU.
    lines += (['#SBATCH --gpus-per-node=2'] if target['gpus'] else
              ['#SBATCH --cpus-per-task=' + str(jobs), '#SBATCH --mem=28G'])
    finalize = shlex.join([*helper, 'preserve', str(run)])
    lines += ['set -eo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""',
              'source /etc/profile.d/lmod.sh', module_load_command(), 'set -u', 'umask 077',
              'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
              'export TMPDIR=' + shlex.quote(str(run / 'runtime')), 'export APPTAINER_TMPDIR="$TMPDIR"',
              'export APPTAINER_CACHEDIR=' + shlex.quote(str(run / 'apptainer-cache')),
              'finish() { rc=$?; trap - EXIT; ' + finalize + ' --exit-code "$rc" || rc=75; exit "$rc"; }',
              'scontrol show job -o "$SLURM_JOB_ID" > ' + shlex.quote(str(run / 'results/allocation.txt'))]
    if target['gpus']:
        lines += ['source /opt/sai_config/mps_mapping.d/' + partition + '.bash',
                  'export APPTAINERENV_CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:?}"',
                  'nvidia-smi -L > ' + shlex.quote(str(run / 'results/gpus.txt'))]
    lines += ['trap finish EXIT', shlex.join([*helper, 'copy', str(run)])]
    if verify_only:
        lines += [shlex.join([*helper, 'copy-overlay', str(run)])]
    else:
        lines += [shlex.join(['apptainer', 'overlay', 'create', '--fakeroot', '--sparse', '--size', '16384', str(overlay)]),
                  shlex.join(command('build'))]
    lines += [
              shlex.join(['/usr/sbin/e2fsck', '-fn', str(overlay)]) + ' > ' + shlex.quote(str(run / 'results/build-fsck.log')) + ' 2>&1',
              shlex.join(['chmod', '400', str(overlay)]),
              shlex.join(command('verify')),
              'echo CP2K_COPIED_SIF_APPLICATION_AND_SINGLE_NODE_SCIENCE_PASSED']
    return '\n'.join(lines) + '\n'


def prepare(args):
    project, controller, run = paths(args.project, args.controller, Path(args.project) / 'runs' / safe_name(args.run_id))
    require(not run.exists(), 'Probe run already exists')
    bundle = Path(args.bundle)
    require(bundle.resolve() == bundle and bundle.is_relative_to(project / 'deployment'), 'Invalid library bundle')
    release = read_json(bundle / 'release.json')
    build = release['builds'][args.partition]
    source = Path(build['sif'])
    require(source.resolve() == source and source.is_relative_to(project / 'runs'), 'Invalid accepted source SIF')
    require(build['library_consumers_verified'] is True, 'Libraries not accepted')
    from cp2k_site_spack import selected
    from software_controller import recipe_fingerprint
    entry = selected(args.partition, lock=controller / 'cp2k_site_spack.json', files=False)
    require(entry and entry['provider']['contract_sha256'] == build['contract_sha256'], 'Wrong candidate provider')
    target_name = PARTITIONS[args.partition]
    identity = make_identity('cp2k', args.track, args.source_ref, args.sha, args.version,
                             recipe_fingerprint('cp2k', controller), target_name)
    subprocess.run(['git', '--git-dir=' + str(project / 'cache/repositories/cp2k'),
                    'cat-file', '-e', args.sha + '^{commit}'], check=True)
    run.mkdir()
    for name in ('results', 'input/cp2k-updates'):
        (run / name).mkdir(parents=True)
    for name in ('runtime', 'apptainer-cache'):
        private_scratch(run / name)
    import cp2k_spack as pins
    archives = {}
    for name, digest in [(pins.WIGNERNJ_ARCHIVE, pins.WIGNERNJ_SHA256),
                         (pins.TBLITE_RELEASE_ARCHIVE, pins.TBLITE_RELEASE_SHA256),
                         (pins.ELPA_ARCHIVE, pins.ELPA_SHA256),
                         (pins.LIBXC_ARCHIVE, pins.LIBXC_SHA256),
                         (pins.LIBXS_ARCHIVE, pins.LIBXS_SHA256)]:
        cached = project / 'cache/source-archives' / digest / name
        require(checksum(cached) == digest, 'Source archive changed: ' + name)
        shutil.copyfile(cached, run / 'input/cp2k-updates' / name)
        require(checksum(run / 'input/cp2k-updates' / name) == digest, 'Copied source archive differs')
        archives[name] = digest
    import cp2k_acceptance as science
    science.ROOT = project
    science.stage_fixtures(run / 'input/science')
    jobs = args.jobs or TARGETS[target_name].get('build_jobs', 8)
    script = render(project, controller, run, identity, node=args.node, jobs=jobs)
    (run / 'job.sbatch').write_text(script)
    subprocess.run(['bash', '-n', str(run / 'job.sbatch')], check=True)
    request = dict(schema=1, purpose=PURPOSE, identity=identity, controller=str(controller),
                   source_sif=str(source), source_sif_sha256=build['sif_sha256'],
                   contract_sha256=build['contract_sha256'], source_archives=archives,
                   controller_files=recipe_files(controller), script_sha256=checksum(run / 'job.sbatch'),
                   bundle_release_sha256=checksum(bundle / 'release.json'), node=args.node, jobs=jobs,
                   physical_installation=False, published=False)
    write_json(run / 'request.json', request)
    response = subprocess.check_output(['sbatch', '--hold', '--parsable', str(run / 'job.sbatch')], text=True, cwd=run)
    job = response.strip().split(';')[0]
    require(job.isdigit(), 'Invalid job response')
    (run / 'job.id').write_text(job + '\n')
    subprocess.run(['scontrol', 'release', job], check=True)
    print(json.dumps(dict(run_id=run.name, job_id=job, identity=identity)), flush=True)


def reverify(args):
    project, controller, run = paths(args.project, args.controller, Path(args.project) / 'runs' / safe_name(args.run_id))
    source = project / 'runs' / safe_name(args.source_run)
    previous = read_json(source / 'request.json')
    require(previous['purpose'] == PURPOSE and not run.exists(), 'Invalid or reused verification source')
    job = (source / 'job.id').read_text().strip()
    state = subprocess.check_output(['sacct', '-X', '-nP', '-j', job, '-o', 'JobIDRaw,State,ExitCode'], text=True)
    rows = [row.split('|')[1:3] for row in state.splitlines() if row.split('|')[0] == job]
    require(len(rows) == 1 and rows[0][0] in ('FAILED', 'COMPLETED'), 'Source job is not terminal')
    require(recipe_files(previous['controller']) == previous['controller_files'], 'Original controller changed')
    records = [read_json(p) for p in (source / 'results').glob('managed-overlay-*.json')]
    builds = [r for r in records if r.get('readonly') is False]
    require(len(builds) == 1 and builds[0].get('accepted') is True, 'No clean completed application build')
    require(all(r.get('completed') is True and r.get('forced_stop') is False
                and r.get('unmount_returncode') == 0 and r.get('driver_returncode') == 0 for r in records),
            'Source overlay teardown was not clean')
    checks = (source / 'results/application-checks.log').read_text()
    summary = json.loads(checks.splitlines()[-1])
    require(summary.get('artifact_checks') is True, 'Source application loader/feature checks did not pass')
    identity = validate_identity(previous['identity'])
    from software_controller import recipe_fingerprint
    require(recipe_fingerprint('cp2k', controller) == identity['recipe_sha256'], 'Application build recipe changed')
    overlay = source / 'work.ext3'
    require(overlay.resolve() == overlay and overlay.is_file() and not overlay.stat().st_mode & 0o222,
            'Application overlay must already be sealed')
    run.mkdir()
    (run / 'results').mkdir()
    for name in ('runtime', 'apptainer-cache'):
        private_scratch(run / name)
    import cp2k_acceptance as fixtures
    fixtures.ROOT = project
    fixtures.stage_fixtures(run / 'input/science')
    request = dict(previous, controller=str(controller), controller_files=recipe_files(controller),
                   source_application_run=source.name, source_overlay=str(overlay),
                   source_overlay_sha256=checksum(overlay), source_request_sha256=checksum(source / 'request.json'))
    script = render(project, controller, run, identity, node=previous['node'], jobs=previous['jobs'],
                    minutes=60, verify_only=True)
    (run / 'job.sbatch').write_text(script)
    subprocess.run(['bash', '-n', str(run / 'job.sbatch')], check=True)
    request['script_sha256'] = checksum(run / 'job.sbatch')
    write_json(run / 'request.json', request)
    response = subprocess.check_output(['sbatch', '--hold', '--parsable', str(run / 'job.sbatch')], text=True, cwd=run)
    job = response.strip().split(';')[0]
    require(job.isdigit(), 'Invalid job response')
    (run / 'job.id').write_text(job + '\n')
    subprocess.run(['scontrol', 'release', job], check=True)
    print(json.dumps(dict(run_id=run.name, job_id=job, source_run=source.name)), flush=True)


def copy_overlay(run):
    request = read_json(run / 'request.json')
    source, destination = Path(request['source_overlay']), run / 'work.ext3'
    require(source.resolve() == source and source.is_file() and not destination.exists(), 'Invalid overlay copy')
    require(not source.stat().st_mode & 0o222, 'Source overlay is not sealed')
    expected = request['source_overlay_sha256']
    require(checksum(source) == expected, 'Source overlay changed')
    subprocess.run(['cp', '--sparse=always', '--reflink=auto', '--', str(source), str(destination)], check=True)
    destination.chmod(0o400)
    require(not os.path.samestat(source.stat(), destination.stat()), 'Overlay copy shares its inode')
    require(checksum(destination) == expected and checksum(source) == expected, 'Overlay copy changed content')
    write_json(run / 'results/copied-application.json', dict(source=str(source), copy=str(destination),
               sha256=expected, recompiled=False))


def preserve(run, exit_code):
    request = read_json(run / 'request.json')
    expected = request['source_sif_sha256']
    record = dict(payload_exit_code=exit_code, source_sha256=checksum(request['source_sif']),
                  copy_sha256=checksum(run / 'dependencies.sif') if (run / 'dependencies.sif').is_file() else None,
                  expected_sha256=expected, published=False)
    record['preserved'] = record['source_sha256'] == expected and record['copy_sha256'] in (None, expected)
    if 'source_overlay' in request:
        record['source_overlay_sha256'] = checksum(request['source_overlay'])
        record['copy_overlay_sha256'] = checksum(run / 'work.ext3')
        record['preserved'] &= record['source_overlay_sha256'] == record['copy_overlay_sha256'] == request['source_overlay_sha256']
    write_json(run / 'results/preservation.json', record)
    require(record['preserved'], 'Dependency SIF changed during application test')


def science(prefix, output):
    from cp2k_acceptance import CASES, parse_science, compare_science
    output.mkdir(parents=True, exist_ok=False)
    samples, comparisons = {}, {}
    env = dict(os.environ, OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
    # Match the public SIF launcher's shared-memory settings; no disk /tmp use.
    env.update(OMPI_MCA_shmem_mmap_backing_file_base_dir='/dev/shm',
               OMPI_MCA_shmem_mmap_relocate_backing_file='1', PRTE_MCA_prte_tmpdir_base='/dev/shm')
    binary = str(prefix / 'bin/cp2k.psmp')
    for case in CASES:
        for ranks in (1, 2):
            key = case + '/' + str(ranks)
            directory = output / key
            shutil.copytree(Path('/input/science/fixtures') / case, directory)
            case_env = dict(env, NCCL_DEBUG='INFO', NCCL_DEBUG_SUBSYS='INIT',
                            NCCL_DEBUG_FILE=str(directory / 'nccl.%h.%p.log'))
            argv = [env['MPI_HOME'] + '/bin/mpiexec', '--allow-run-as-root', '--bind-to', 'none',
                    '-n', str(ranks), binary, '-i', 'input.inp']
            print('CP2K_APPLICATION_SCIENCE ' + key, flush=True)
            start = time.monotonic()
            with (directory / 'cp2k.log').open('x') as log, (directory / 'mpi.stderr').open('x') as errors:
                completed = subprocess.run(argv, env=case_env, cwd=directory, stdout=log,
                                           stderr=errors, timeout=600)
            elapsed = time.monotonic() - start
            write_json(directory / 'execution.json', dict(argv=argv, returncode=completed.returncode,
                       elapsed_seconds=elapsed, binary_sha256=checksum(binary), mpi_ranks=ranks))
            require(completed.returncode == 0, 'Scientific case failed: ' + key)
            sample = parse_science((directory / 'cp2k.log').read_text(),
                                   (directory / 'forces.xyz').read_text(), str(elapsed), case)
            samples[key] = sample
            write_json(directory / 'sample.json', sample)
        comparisons[case] = compare_science(samples[case + '/2'], samples[case + '/1'])
    write_json(output / 'passed.json', dict(single_node=True, mpi_ranks=[1, 2], samples=samples,
               comparisons=comparisons, multinode_verified=False, published=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='op', required=True)
    submit = commands.add_parser('submit')
    for name in ('project', 'controller', 'bundle'):
        submit.add_argument(name, type=Path)
    submit.add_argument('run_id')
    submit.add_argument('partition', choices=tuple(PARTITIONS))
    for name in ('sha', 'version', 'source-ref'):
        submit.add_argument('--' + name, required=True)
    submit.add_argument('--track', choices=('release', 'development'), default='release')
    submit.add_argument('--node')
    submit.add_argument('--jobs', type=int)
    retry = commands.add_parser('reverify')
    retry.add_argument('project', type=Path)
    retry.add_argument('controller', type=Path)
    retry.add_argument('source_run')
    retry.add_argument('run_id')
    for op in ('copy', 'copy-overlay', 'preserve'):
        child = commands.add_parser(op)
        child.add_argument('run', type=Path)
        if op == 'preserve':
            child.add_argument('--exit-code', type=int, required=True)
    check = commands.add_parser('science')
    check.add_argument('prefix', type=Path)
    check.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.op == 'submit':
        prepare(args)
    elif args.op == 'reverify':
        reverify(args)
    elif args.op == 'copy-overlay':
        copy_overlay(args.run)
    elif args.op == 'copy':
        request = read_json(args.run / 'request.json')
        require(request['controller_files'] == recipe_files(request['controller']), 'Frozen controller changed')
        require(checksum(args.run / 'job.sbatch') == request['script_sha256'], 'Probe job changed')
        write_json(args.run / 'results/copied-sif.json', copy_image(request['source_sif'],
                   args.run / 'dependencies.sif', request['source_sif_sha256']))
    elif args.op == 'preserve':
        preserve(args.run, args.exit_code)
    else:
        science(args.prefix, args.output)


if __name__ == '__main__':
    main()
