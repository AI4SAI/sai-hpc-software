#!/usr/bin/env python3
"""Build a separately versioned NEPAdapters SIF for the installed LAMMPS release.

All compilation happens in a new project-backed overlay. The accepted LAMMPS
build overlay and physical installations are read-only dependencies.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess

from cp2k_image_driver import driver_path, wrap_command, module_load_command
from remote_controller import (SAI_PROJECT_ROOT, container_command, private_scratch,
                               project_batch_directives, project_qos, safe_name, site_app_binds)
from source_cache import checksum

VERSION = '1.0.4'
SOURCE_SHA = 'c934e71444cb4099d63dff98478f02c86373b061'
SOURCE_ARCHIVE_SHA256 = 'e7646bd1b79ce5bacaa241689326bd7fc224b02c10df70e6cf4cff7395fbe320'
LAMMPS_SHA = '8de817dd79bfe4525d5d39246a212d833e6dee07'
MODULE = 'lammps/30Sep2026-deepmd320-auto'
SHARED_PREFIX = '/opt/apps/nepadapters/1.0.4-lammps30Sep2026'
PAYLOAD = ('lib/nepadapterscpuplugin.so', 'lib/nepadaptersgpuplugin.so',
           'share/licenses/nepadapters/LICENSE.NEP_CPU')
PARTITIONS = {'16V100': '16v100-avx2', '4V100': '4v100-avx512', '8V100V0': '8v100v0-avx512'}
CONTROL = Path(__file__).resolve().parent
FILES = ('nepadapters_release.py', 'nepadapters_build.sh', 'nepadapters_compat.py',
         'nepadapters_science.py', 'md_environment.sh', 'cp2k_image_driver.py',
         'cp2k_overlay_sync.py', 'remote_controller.py', 'source_cache.py', 'create_rootfs.sh')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def dependency(deployment, partition):
    plan = json.loads((deployment / 'runtime-plan.json').read_text())
    release = json.loads((deployment / 'release.json').read_text())
    result = {}
    for software in ('lammps', 'deepmd-kit'):
        rows = [x for x in plan['installations'] if x['identity']['software'] == software
                and x['identity']['partition'] == partition]
        require(len(rows) == 1, 'missing installed dependency for ' + partition)
        row = rows[0]
        prefix = Path(row['prefix'])
        require(prefix.resolve() == prefix, 'redirected dependency prefix')
        pins = {}
        for f in row['files']:
            if f['kind'] == 'file' and (f['path'] == 'bin/lmp' or 'liblammps.so' in f['path']):
                path = prefix / f['path']
                require(checksum(path) == f['sha256'], 'installed LAMMPS changed: ' + str(path))
                pins[f['path']] = f['sha256']
        result['lammps' if software == 'lammps' else 'deepmd'] = dict(
            prefix=str(prefix), identity=row['identity'], files=pins)
    require(result['lammps']['identity']['source_sha'] == LAMMPS_SHA, 'unexpected LAMMPS release')
    row = next(x for x in release['deliveries'] if x['partition'] == partition)
    home = deployment.parent.parent
    overlay = home / 'experimental/deepmd-lammps/runs' / row['build_run'] / 'work.ext3'
    require(overlay.is_file() and overlay.resolve() == overlay, 'original LAMMPS build overlay unavailable')
    result['lammps_overlay'] = str(overlay)
    return result


def runtime_request(request, partition):
    """Select host dependencies while preserving the single shared plugin/SIF."""
    require(partition in PARTITIONS, 'unsupported partition')
    if request.get('layout') != 'shared':
        require(partition == request['partition'], 'legacy plugin requires its build partition')
        return request
    require(request['prefix'] == SHARED_PREFIX and
            set(request['partition_dependencies']) == set(PARTITIONS), 'incomplete shared build dependencies')
    return dict(request, partition=partition, target=PARTITIONS[partition],
                **request['partition_dependencies'][partition])


def render(run, request):
    root = run.parent.parent
    control = run / 'control'
    overlay = run / 'work.ext3'
    image = run / 'nepadapters.sif'
    base = Path(request['project']) / 'containers/base/minimal-v1.sif'
    binds = [*site_app_binds('lammps'), ('/etc/alternatives', '/etc/alternatives'),
             (request['lammps']['prefix'], request['lammps']['prefix']),
             (request['deepmd']['prefix'], request['deepmd']['prefix']),
             (run / 'request.json', '/input/request.json')]

    def command(phase):
        final = phase == 'verify'
        extra = binds if final else [*binds, (request['source_archive'], '/input/nepadapters.tar.gz')]
        argv = container_command(image if final else base,
            ['/usr/bin/bash', '/control/nepadapters_build.sh', request['target'], phase],
            overlay=None if final else overlay, control=control, jobs=12,
            gpu=True, runtime=run / 'runtime', extra_binds=extra)
        if final:
            return [x for x in argv if x != '--fakeroot']
        argv[2:2] = ['--overlay', request['lammps_overlay'] + ':ro']
        return wrap_command(root, run, control, argv)

    emit = container_command(base, ['/usr/bin/cat', '/workspace/nepadapters-final.squashfs'],
                             overlay=str(overlay) + ':ro', jobs=12)
    emit = wrap_command(root, run, control, emit)
    export = container_command(image, ['/usr/bin/tar', '-C', request['prefix'], '-cf', '-', '.'],
                               jobs=1)
    export = [x for x in export if x != '--fakeroot']
    j = shlex.join
    lines = ['#!/usr/bin/env bash', '#SBATCH --job-name=nepadapters-' + request['partition'],
             '#SBATCH --partition=' + request['partition'],
             '#SBATCH --qos=' + project_qos(Path(request['project']), request['target']),
             *project_batch_directives(Path(request['project']), run),
             '#SBATCH --nodes=1', '#SBATCH --ntasks=1',
             '#SBATCH --gpus-per-node=2', '#SBATCH --time=30', '#SBATCH --export=NIL',
             '#SBATCH --output=' + str(run / 'results/slurm-%j.log'),
             'set -eo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""',
             'export USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?}',
             'source /etc/profile.d/lmod.sh', module_load_command(), 'set -u',
             'export TMPDIR=' + shlex.quote(str(run / 'runtime')),
             'export APPTAINER_TMPDIR="$TMPDIR"',
             'export APPTAINER_CACHEDIR=' + shlex.quote(str(run / 'apptainer-cache')),
             'export APPTAINERENV_SAI_MD_ALLOCATED_JOB="$SLURM_JOB_ID"',
             'export APPTAINERENV_SAI_MD_ALLOCATED_NODE="$(hostname)"',
             'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
             j(['/usr/bin/python3', '-B', str(control / 'nepadapters_release.py'), 'verify-input', str(run)]),
             j(['apptainer', 'overlay', 'create', '--fakeroot', '--sparse', '--size', '8192', str(overlay)]),
             j(command('build')),
             j(['/usr/sbin/e2fsck', '-fn', str(overlay)]),
             j(emit) + ' > ' + shlex.quote(str(run / 'final.squashfs')),
             j(['apptainer', 'sif', 'new', str(image)]),
             j(['apptainer', 'sif', 'add', '--datatype', '4', '--partfs', '1', '--parttype', '2',
                '--partarch', '2', '--groupid', '1', str(image), str(run / 'final.squashfs')]),
             j(command('verify')),
             j(export) + ' | ' + j(['tar', '-C', str(run / 'export'), '-xf', '-']),
             j(['chmod', '0444', str(image)]),
             j(['chmod', '-R', 'a+rX,go-w', str(run / 'export')]),
             j(['/usr/bin/python3', '-B', str(control / 'nepadapters_release.py'), 'finalize', str(run)]),
             'echo NEPADAPTERS_SIF_BUILD_AND_SINGLE_GPU_ACCEPTED']
    return '\n'.join(lines) + '\n'


def submit(project, deployment, source, run_id, partition):
    project, deployment, source = project.resolve(), deployment.resolve(), source.resolve()
    require(project.is_relative_to(SAI_PROJECT_ROOT), 'use the funded project directory')
    require(source.is_relative_to(project) and source.is_file(), 'cache the pinned source in the project')
    require(checksum(source) == SOURCE_ARCHIVE_SHA256, 'source archive differs from git archive of the pinned release')
    root = project / 'experimental/nepadapters'
    run = root / 'runs' / safe_name(run_id)
    require(not run.exists(), 'run exists; inspect it instead of submitting duplicate work')
    dep = dependency(deployment, partition)
    pins = {name: checksum(CONTROL / name) for name in FILES}
    recipe = hashlib.sha256(json.dumps(dict(files=pins, source_sha=SOURCE_SHA), sort_keys=True).encode()).hexdigest()
    request = dict(schema=1, software='nepadapters', version=VERSION, source_sha=SOURCE_SHA,
                   source_archive=str(source), source_archive_sha256=checksum(source),
                   recipe_sha256=recipe, controller_files=pins, project=str(project),
                   partition=partition, target=PARTITIONS[partition], lammps_module=MODULE,
                   layout='shared', prefix=SHARED_PREFIX,
                   partition_dependencies={p: dependency(deployment, p) for p in PARTITIONS}, **dep)
    (run / 'results').mkdir(parents=True)
    (run / 'export').mkdir()
    (run / 'control').mkdir()
    for name in ('runtime', 'apptainer-cache'):
        private_scratch(run / name)
    for name in FILES:
        shutil.copyfile(CONTROL / name, run / 'control' / name)
        (run / 'control' / name).chmod(0o444)
    driver = driver_path(root)
    if not driver.exists():
        driver.parent.mkdir(parents=True)
        shutil.copyfile(driver_path(project / 'experimental/deepmd-lammps'), driver)
        driver.chmod(0o555)
    (run / 'request.json').write_text(json.dumps(request, indent=2) + '\n')
    script = run / 'job.sbatch'
    script.write_text(render(run, request))
    for path in (run / 'request.json', script):
        path.with_suffix(path.suffix + '.sha256').write_text(checksum(path) + '\n')
        path.chmod(0o444)
    subprocess.run(['bash', '-n', str(script)], check=True)
    subprocess.run(['sbatch', '--test-only', str(script)], cwd=run, check=True)
    job = subprocess.check_output(['sbatch', '--parsable', str(script)], cwd=run, text=True).strip().split(';')[0]
    require(job.isdigit(), 'invalid Slurm job id')
    (run / 'job.id').write_text(job + '\n')
    print(json.dumps(dict(run=str(run), job=job, partition=partition, recipe=recipe)), flush=True)


def context(run):
    request = json.loads((run / 'request.json').read_text())
    for path in (run / 'request.json', run / 'job.sbatch'):
        require(checksum(path) == path.with_suffix(path.suffix + '.sha256').read_text().strip(),
                'frozen request/job script changed')
    for name, sha in request['controller_files'].items():
        require(Path(name).name == name, 'invalid controller name')
        require(checksum(run / 'control' / name) == sha, 'frozen build control changed')
    require(request['source_sha'] == SOURCE_SHA and request['version'] == VERSION, 'wrong NEP release')
    recipe = hashlib.sha256(json.dumps(dict(files=request['controller_files'], source_sha=SOURCE_SHA),
                                      sort_keys=True).encode()).hexdigest()
    part = request['partition']
    prefix = SHARED_PREFIX if request.get('layout') == 'shared' else f'/opt/apps/nepadapters/{VERSION}-lammps30Sep2026-r{recipe[:12]}/{part}'
    require(request['recipe_sha256'] == recipe and request['target'] == PARTITIONS[part] and
            request['prefix'] == prefix,
            'build recipe/prefix changed')
    require(runtime_request(request, part) == request, 'build dependency selection differs')
    require(request['lammps']['identity']['source_sha'] == LAMMPS_SHA and
            request['lammps']['identity']['partition'] == part and
            'bin/lmp' in request['lammps']['files'] and
            any('liblammps.so' in f for f in request['lammps']['files']), 'missing LAMMPS ABI pins')
    for name, sha in request['lammps']['files'].items():
        require(checksum(Path(request['lammps']['prefix']) / name) == sha, 'installed LAMMPS changed')
    return request


def verify_input(run):
    request = context(run)
    require(checksum(Path(request['source_archive'])) == SOURCE_ARCHIVE_SHA256 ==
            request['source_archive_sha256'], 'pinned source archive changed')


def artifact_record(run):
    from nepadapters_science import verify
    request = context(run)
    files = {name: checksum(run / 'export' / name) for name in PAYLOAD}
    for phase in ('science-build', 'science-sif'):
        proof = verify(run / 'runtime/cases', run / 'runtime' / phase)
        require(proof['lmp'] == request['lammps']['prefix'] + '/bin/lmp' and
                proof['plugins'] == request['prefix'] + '/lib' and
                proof['plugin_sha256'] == {Path(k).name: v for k, v in files.items() if k.startswith('lib/')},
                'tested plugin/LAMMPS differs from SIF export')
    return dict(schema=1, request_sha256=checksum(run / 'request.json'),
                image_sha256=checksum(run / 'nepadapters.sif'), files=files,
                science_sha256={phase: checksum(run / 'runtime' / phase / 'acceptance.json')
                                for phase in ('science-build', 'science-sif')})


def build_report(run):
    request = context(run)
    job = (run / 'job.id').read_text().strip()
    require(job.isdigit(), 'invalid job handle')
    output = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', job,
                                      '-o', 'JobIDRaw,State,ExitCode'], text=True)
    row = next((x.split('|') for x in output.splitlines() if x.split('|')[0] == job), None)
    result = dict(job=job, partition=request['partition'], accounting=row, accepted=False)
    if row and row[1:3] == ['COMPLETED', '0:0']:
        log = (run / f'results/slurm-{job}.log').read_text()
        require('NEPADAPTERS_SIF_BUILD_AND_SINGLE_GPU_ACCEPTED' in log, 'missing successful build marker')
        receipt = json.loads((run / 'results/artifacts.json').read_text())
        require(receipt == artifact_record(run), 'SIF/export changed after acceptance')
        result.update(accepted=True, scope='single-GPU; native multi-GPU acceptance still required',
                      image=str(run / 'nepadapters.sif'), image_sha256=receipt['image_sha256'], artifacts=receipt)
    return result


def status(run):
    result = build_report(run)
    (run / 'results/status.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)
    row = result['accounting']
    active = ('PENDING', 'RUNNING', 'CONFIGURING', 'COMPLETING', 'SUSPENDED', 'REQUEUED')
    return 0 if result['accepted'] else (3 if not row or row[1] in active else 1)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest='command', required=True)
    s = commands.add_parser('submit')
    s.add_argument('--project', type=Path, default=SAI_PROJECT_ROOT)
    s.add_argument('--deployment', type=Path, default=Path('/home/stardust/sai-hpc-software/deployment/lammps-30Sep2026'))
    s.add_argument('--source', type=Path, required=True)
    s.add_argument('--run-id', required=True)
    s.add_argument('--partition', choices=PARTITIONS, required=True)
    s = commands.add_parser('status')
    s.add_argument('run', type=Path)
    for name in ('verify-input', 'finalize'):
        s = commands.add_parser(name)
        s.add_argument('run', type=Path)
    args = p.parse_args()
    if args.command == 'submit':
        submit(args.project, args.deployment, args.source, args.run_id, args.partition)
    elif args.command == 'status':
        raise SystemExit(status(args.run))
    elif args.command == 'verify-input':
        verify_input(args.run)
    else:
        receipt = args.run / 'results/artifacts.json'
        with receipt.open('x') as stream:
            json.dump(artifact_record(args.run), stream, indent=2)
        receipt.chmod(0o444)
