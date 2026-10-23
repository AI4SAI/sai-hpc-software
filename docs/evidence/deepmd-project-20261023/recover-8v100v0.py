#!/usr/bin/env python3
"""Export an independently checked copy of the completed DeePMD 8V100V0 build.

The original failed allocation and its recipe remain immutable. The recovered
SIF uses that recipe; a separately pinned checker validates scientific output.
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
ROOT = PROJECT / 'experimental/deepmd-standalone'
ACCOUNT = 'proj-sai-hpc-software'
SOURCE_SHA = '558f56962775098b8b554e341c3b37d2f86e6aeb'
SOURCE_RECIPE = 'e04065e624bdf113dbcc32e713510472a1416bec81e664571ce84121024d54ea'
CHECKER_COMMIT = 'd8b4c2fe338dbc8626bb78ffbebbf69bda572029'
CHECKER = ROOT / 'controller' / CHECKER_COMMIT / 'controller'
DRIVER_SHA = '46c1a8c8f42ab2419ea88072e41d89903c453d93f52485b5baddea5ca238bb10'


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(8 * 1024 * 1024):
            result.update(block)
    return result.hexdigest()


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


def controller_files(control):
    require(not any(p.is_symlink() for p in control.iterdir()), 'symlink in frozen controller')
    return {p.name: digest(p) for p in sorted(control.iterdir()) if p.is_file()}


def validate_original(source):
    request = read(source / 'request.json')
    control = canonical(request['controller'])
    require(control.is_relative_to(ROOT / 'controller'), 'original controller escaped project')
    code = '''import json,sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from deepmd_standalone import CONTROL,fingerprint,render,validate_delivery
from source_cache import checksum
source=Path(sys.argv[2])
request=json.loads((source/'request.json').read_text())
validate_delivery(request['delivery'])
assert request['controller']==str(CONTROL)
assert request['controller_sha256']==fingerprint()
assert request['job_script_sha256']==checksum(source/'job.sbatch')
assert (source/'job.sbatch').read_text()==render(request['delivery'],source.name)
'''
    subprocess.run(['/usr/bin/python3', '-B', '-c', code, str(control), str(source)], check=True,
        env=dict(os.environ, SAI_SOFTWARE_ROOT=str(PROJECT), PYTHONDONTWRITEBYTECODE='1'))
    identity = request['delivery']['identity']
    require(identity['source_sha'] == SOURCE_SHA and identity['recipe_sha256'] == SOURCE_RECIPE
            and identity['target'] == '8v100v0-avx512', 'unexpected original source/recipe/target')
    row = accounting(request['job_id'])
    require(row[1:] == ['FAILED', '255:0', '8V100V0', ACCOUNT, str(source)], 'unexpected original failure')
    log = (source / ('results/slurm-' + request['job_id'] + '.log')).read_text()
    require('fuse2fs failed to mount' in log and '\nnull\nCP2K_IMAGE_DRIVER' in log
            and '"prepared_backends": ["tf", "pt", "jax"]' in log,
            'source did not finish fixtures/manifest before the export mount failure')
    require(not (source / 'candidate.sif').exists(), 'source already has an exported candidate')
    return request, row


def input_files(runtime):
    paths = [runtime / name for name in ('runtime-env.sh', 'baseline-inventory.json',
             'candidate-inventory.json', 'CMakeCache.txt')]
    paths += sorted((runtime / 'case').rglob('*'))
    require(not any(p.is_symlink() for p in paths), 'redirected runtime input')
    return {str(p.relative_to(runtime)): digest(p) for p in paths if p.is_file()}


def prepare(source, run):
    source, run = canonical(source), canonical(run)
    require(source.parent == run.parent == ROOT / 'runs' and source != run, 'invalid recovery run roots')
    require(not run.exists(), 'recovery run already exists')
    original, row = validate_original(source)
    run.mkdir(parents=True)
    (run / 'results').mkdir()
    for name in ('runtime', 'apptainer-cache'):
        private(run / name)
    control_root = ROOT / 'controller/deepmd-recovery-20261023' / run.name
    pins = {}
    for label, control in (('original', Path(original['controller'])), ('acceptance', CHECKER)):
        dest = control_root / label
        dest.mkdir(parents=True)
        pins[label] = controller_files(canonical(control))
        for name in pins[label]:
            shutil.copyfile(control / name, dest / name)
            (dest / name).chmod(0o444)
        require(controller_files(dest) == pins[label], 'frozen controller copy differs')
    inputs = input_files(source / 'runtime')
    for name, checksum in inputs.items():
        dest = run / 'runtime' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / 'runtime' / name, dest)
        require(digest(dest) == checksum, 'runtime fixture copy differs')
    driver = ROOT / 'cache/tools' / ('fuse2fs-' + DRIVER_SHA) / 'bin/fuse2fs'
    require(canonical(driver).is_file() and digest(driver) == DRIVER_SHA and
            not driver.stat().st_mode & 0o222, 'project overlay driver differs')
    print('Hashing completed source overlay', flush=True)
    overlay_sha = digest(source / 'work.ext3')
    print('Copying completed overlay into a separate project recovery run', flush=True)
    subprocess.run(['cp', '--reflink=never', '--sparse=always', '--',
                    str(source / 'work.ext3'), str(run / 'work.ext3')], check=True)
    (run / 'work.ext3').chmod(0o600)
    require(digest(run / 'work.ext3') == overlay_sha, 'overlay copy checksum mismatch')
    job = original['job_id']
    record = dict(schema=1, operation='recover-deepmd-export-and-science', source=str(source),
        source_job=job, source_accounting=row, source_overlay_sha256=overlay_sha,
        source_files={name: digest(source / name) for name in
            ('request.json', 'job.sbatch', 'results/slurm-' + job + '.log')},
        source_controller=original['controller'], source_controller_files=pins['original'],
        delivery=original['delivery'], controller=str(control_root / 'original'),
        acceptance_controller=str(control_root / 'acceptance'), acceptance_commit=CHECKER_COMMIT,
        acceptance_controller_files=pins['acceptance'], runtime_inputs=inputs,
        copied_overlay_sha256=overlay_sha, base_sha256=digest(PROJECT / 'containers/base/minimal-v1.sif'),
        driver_sha256=DRIVER_SHA, recovery_helper_sha256=digest(__file__))
    write(run / 'request.json', record)
    (run / 'request.sha256').write_text(digest(run / 'request.json') + '\n')
    print(json.dumps(dict(prepared=str(run), source_overlay_sha256=overlay_sha)), flush=True)


def context(run):
    run = canonical(run)
    require(run.parent == ROOT / 'runs', 'recovery escaped project')
    require(digest(run / 'request.json') == (run / 'request.sha256').read_text().strip(), 'recovery request changed')
    record = read(run / 'request.json')
    require(record['schema'] == 1 and record['operation'] == 'recover-deepmd-export-and-science', 'wrong recovery')
    require(digest(__file__) == record['recovery_helper_sha256'], 'recovery helper changed')
    source = canonical(record['source'])
    require(source.parent == ROOT / 'runs', 'source escaped project')
    original, row = validate_original(source)
    require(row == record['source_accounting'] and original['delivery'] == record['delivery'], 'source identity changed')
    for name, checksum in record['source_files'].items():
        require(digest(source / name) == checksum, 'failed source evidence changed: ' + name)
    require(controller_files(canonical(record['source_controller'])) == record['source_controller_files'],
            'original controller changed')
    require(controller_files(canonical(record['controller'])) == record['source_controller_files'],
            'frozen controller copy changed')
    require(controller_files(canonical(record['acceptance_controller'])) == record['acceptance_controller_files'],
            'scientific checker changed')
    require(input_files(source / 'runtime') == record['runtime_inputs'], 'original runtime inputs changed')
    for name, checksum in record['runtime_inputs'].items():
        require(digest(run / 'runtime' / name) == checksum, 'copied runtime input changed: ' + name)
    require(digest(PROJECT / 'containers/base/minimal-v1.sif') == record['base_sha256'], 'base image changed')
    return record


def render(run):
    record = context(run)
    control = Path(record['controller'])
    acceptance = Path(record['acceptance_controller'])
    sys.path.insert(0, str(control))
    from remote_controller import container_command, site_app_binds
    from cp2k_image_driver import wrap_command
    base = PROJECT / 'containers/base/minimal-v1.sif'
    image, overlay = run / 'candidate.sif', run / 'work.ext3'
    payload = json.dumps(record['delivery'], sort_keys=True, separators=(',', ':'))

    def command(phase):
        final = phase in ('verify', 'science')
        if phase == 'science':
            shell = ('source /runtime/runtime-env.sh; export SAI_DEEPMD_DELIVERY="$2"; '
                     'export OMP_NUM_THREADS=1 DP_INTRA_OP_PARALLELISM_THREADS=1 DP_INTER_OP_PARALLELISM_THREADS=1; '
                     'exec "$1/bin/python" /acceptance/deepmd_standalone_check.py science')
            args = ['/usr/bin/bash', '-euo', 'pipefail', '-c', shell, 'bash',
                    record['delivery']['identity']['install_prefix'], payload]
        else:
            args = ['/usr/bin/bash', '/control/deepmd_standalone_entry.sh', phase, payload]
        argv = container_command(image if final else base, args,
            overlay=None if final else overlay, control=control, jobs=12, gpu=True,
            runtime=run / 'runtime', extra_binds=[*site_app_binds('deepmd-kit'),
                                                *([(acceptance, '/acceptance')] if phase == 'science' else [])])
        position = argv.index(str(image if final else base))
        argv[position:position] = ['--bind', str(run / 'runtime') + ':/tmp:rw']
        return ([arg for arg in argv if arg != '--fakeroot'] if final else
                wrap_command(ROOT, run, control, argv))

    emit_argv = container_command(base, ['/usr/bin/cat', '/workspace/final.squashfs'],
        overlay=str(overlay) + ':ro', jobs=12, runtime=run / 'runtime')
    emit_argv[emit_argv.index(str(base)):emit_argv.index(str(base))] = ['--bind', str(run / 'runtime') + ':/tmp:rw']
    emit = wrap_command(ROOT, run, control, emit_argv)
    lines = ['#!/usr/bin/env bash', '#SBATCH --job-name=deepmd-export-recovery-8V100V0',
        '#SBATCH --partition=8V100V0', '#SBATCH --qos=rush-1o2gpu', '#SBATCH --account=' + ACCOUNT,
        '#SBATCH --chdir=' + str(run), '#SBATCH --nodes=1', '#SBATCH --ntasks=1',
        '#SBATCH --gpus-per-node=2', '#SBATCH --time=40', '#SBATCH --export=NIL',
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
        'echo DEEPMD_EXPORT_RECOVERY_BUILD_VERIFIED', shlex.join(command('science')),
        'echo DEEPMD_EXPORT_RECOVERY_SCIENCE_VERIFIED_NOT_PUBLISHED']
    return '\n'.join(lines) + '\n'


def submit(run, tools_commit):
    context(run)
    require(re.fullmatch(r'[a-f0-9]{40}', tools_commit), 'full recovery tools commit required')
    require(not (run / 'job.id').exists(), 'recovery already submitted')
    script = run / 'job.sbatch'
    with script.open('x') as stream:
        stream.write(render(run))
    subprocess.run(['bash', '-n', str(script)], check=True)
    subprocess.run(['sbatch', '--test-only', str(script)], cwd=run, check=True)
    write(run / 'submission.json', dict(script_sha256=digest(script), recovery_tools_commit=tools_commit))
    job = subprocess.check_output(['sbatch', '--hold', '--parsable', str(script)], cwd=run, text=True).strip()
    require(re.fullmatch(r'[1-9][0-9]*', job), 'invalid recovery job')
    (run / 'job.id').write_text(job + '\n')
    subprocess.run(['scontrol', 'release', job], check=True)
    print(json.dumps(dict(job=job, run=str(run), account=ACCOUNT)), flush=True)


def science_files(control, runtime):
    code = '''import json,sys
sys.path.insert(0, sys.argv[1])
from deepmd_standalone_check import verify_science_files
print(json.dumps(verify_science_files(sys.argv[2]), sort_keys=True))
'''
    return json.loads(subprocess.check_output(['/usr/bin/python3', '-B', '-c', code, str(control), str(runtime)], text=True))


def status(run, verify_source=False):
    record = context(run)
    job = (run / 'job.id').read_text().strip()
    row = accounting(job)
    require(row[1:6] == ['COMPLETED', '0:0', '8V100V0', ACCOUNT, str(run)], 'recovery not completed: ' + str(row))
    require(digest(run / 'job.sbatch') == read(run / 'submission.json')['script_sha256'], 'recovery script changed')
    log = (run / ('results/slurm-' + job + '.log')).read_text()
    require('DEEPMD_EXPORT_RECOVERY_BUILD_VERIFIED' in log and
            'DEEPMD_EXPORT_RECOVERY_SCIENCE_VERIFIED_NOT_PUBLISHED' in log, 'missing export/science verification')
    if verify_source:
        require(digest(Path(record['source']) / 'work.ext3') == record['source_overlay_sha256'], 'original overlay changed')
    image = canonical(run / 'candidate.sif')
    require(not image.stat().st_mode & 0o222, 'recovered SIF must be immutable')
    science = science_files(record['acceptance_controller'], run / 'runtime')
    require(science == read(run / 'runtime/science.json'), 'raw scientific evidence changed')
    return dict(schema=1, operation=record['operation'], job_id=job, state=row[1], exit_code=row[2],
        account=row[4], workdir=row[5], source_job=record['source_job'], source_job_state='FAILED',
        source_overlay_sha256=record['source_overlay_sha256'], delivery=record['delivery'],
        build_verified=True, scientific_verified=True, native_runtime_verified=False, published=False,
        artifact=str(image), artifact_sha256=digest(image), science=science,
        request_sha256=digest(run / 'request.json'), acceptance_commit=record['acceptance_commit'],
        recovery_tools_commit=read(run / 'submission.json')['recovery_tools_commit'])


if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare', 'submit', 'status'))
    parser.add_argument('run', type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--tools-commit')
    parser.add_argument('--verify-source', action='store_true')
    args = parser.parse_args()
    if args.operation == 'prepare':
        require(args.source is not None, 'source run required')
        prepare(args.source, args.run)
    elif args.operation == 'submit':
        submit(args.run, args.tools_commit or '')
    else:
        result = status(args.run, args.verify_source)
        output = args.run / 'results/status.json'
        if not output.exists():
            write(output, result)
        require(read(output) == result, 'recovery result changed')
        print(json.dumps(result, sort_keys=True), flush=True)
