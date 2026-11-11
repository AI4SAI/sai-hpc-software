#!/usr/bin/env python3
"""Submit a pinned NepTrainKit build at its final prefix inside Apptainer."""
import argparse
from datetime import datetime
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
ROOT = PROJECT / 'experimental/neptrainkit'
PREFIX = '/opt/apps/neptrainkit/3.1.5'
SOURCE = '578159e74cc23ae682787593b6aec3997b590fca'
SOURCE_SHA256 = 'cfc46e94516d98c4b45135fcdfe569e2a6b6c2ee6a3fd6545f1c8363bc6864e1'
PACKAGE = Path(__file__).resolve().parent
BASE = PROJECT / 'containers/base/minimal-v1.sif'
APP = '/opt/devtools/apptainer/1.4.4/bin/apptainer'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True); stream.write('\n')


def private(path):
    path.mkdir(parents=True, exist_ok=True)
    for name in os.listxattr(path):
        if name in ('system.posix_acl_access', 'system.posix_acl_default'):
            os.removexattr(path, name)
    path.chmod(0o700)


def offset(image):
    result = subprocess.check_output([APP, 'sif', 'list', str(image)], text=True)
    rows = re.findall(r'^\s*\d+\s*\|[^\n|]*\|[^\n|]*\|\s*(\d+)-(\d+)\s*\|\s*FS \(Squashfs/\*System/[^)]+\)\s*$', result, re.M)
    assert len(rows) == 1, result
    return int(rows[0][0])


def container(run, image, command, building=False):
    argv = [APP, 'exec', '--cleanenv', '--containall', '--no-home', '--no-mount',
            'bind-paths,home,cwd,tmp,hostfs', '--pwd', '/runtime', '--nv']
    for path in ('/usr', '/sbin', '/lib', '/lib64', '/opt/devtools', '/etc/alternatives', '/etc/fonts'):
        argv += ['--bind', path + ':' + path + ':ro']
    argv += ['--bind', str(run / 'control') + ':/control:ro',
             '--bind', str(run / 'runtime') + ':/runtime:rw']
    for name, value in dict(TMPDIR='/runtime/tmp',
            XDG_CACHE_HOME='/runtime/cache', XDG_CONFIG_HOME='/runtime/config',
            XDG_RUNTIME_DIR='/runtime/xdg', QT_QPA_PLATFORM='offscreen',
            LOCALAPPDATA='/runtime/localappdata', NEPTRAINKIT_RUNTIME_ROOT='/runtime/managed-nep',
            PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1',
            OMP_NUM_THREADS='4', OPENBLAS_NUM_THREADS='1',
            LD_LIBRARY_PATH='/opt/devtools/gcc/14.3.0/lib64:/opt/devtools/gcc/14.3.0/lib').items():
        argv += ['--env', name + '=' + value]
    if building:
        argv += ['--bind', str(run / 'rootfs' / PREFIX.lstrip('/')) + ':' + PREFIX + ':rw',
                 '--bind', str(run / 'workspace') + ':/workspace:rw',
                 '--bind', str(ROOT / 'cache/wheels') + ':/input/wheels:ro',
                 '--bind', str(ROOT / 'cache/source' / ('NepTrainKit-' + SOURCE + '.tar.gz')) + ':/input/source.tar.gz:ro',
                 '--bind', str(PROJECT / 'cache/python-envs-20261104-a') + ':/input/seed:ro',
                 '--bind', str(PROJECT / 'cache/python-envs-20261104-a/conda') + ':/input/packages/conda:ro']
    return argv + [str(image), '/usr/bin/env', 'HOME=/runtime/home', *command]


def submit():
    source = ROOT / 'cache/source' / ('NepTrainKit-' + SOURCE + '.tar.gz')
    assert digest(source) == SOURCE_SHA256
    wheels = sorted((ROOT / 'cache/wheels').glob('*.whl'))
    assert len(wheels) >= 30
    run = ROOT / 'runs' / ('release-3.1.5-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    run.mkdir(parents=True)
    for name in ('control', 'runtime', 'workspace', 'results'):
        private(run / name)
    subprocess.run(['unsquashfs', '-processors', '2', '-no-progress', '-o', str(offset(BASE)),
                    '-d', str(run / 'rootfs'), str(BASE)], check=True, stdout=subprocess.DEVNULL)
    private(run / 'rootfs' / PREFIX.lstrip('/'))
    (run / 'rootfs' / PREFIX.lstrip('/')).chmod(0o755)
    for name in ('build.sh', 'science.py', 'gui-smoke.py', 'prepare.py'):
        shutil.copyfile(PACKAGE / name, run / 'control' / name)
        (run / 'control' / name).chmod(0o444)
    seed = PROJECT / 'cache/python-envs-20261104-a'
    seed_records = json.loads((seed / 'python-base.json').read_text())
    for row in seed_records:
        assert digest(seed / 'conda' / row['url'].rsplit('/', 1)[1]) == row['sha256']
    pins = {str(path): digest(path) for path in [source, BASE, *wheels,
            seed / 'python-explicit.txt', *[seed / 'conda' / row['url'].rsplit('/',1)[1] for row in seed_records],
            *sorted((run / 'control').iterdir())]}
    save(run / 'request.json', dict(version='3.1.5', source=SOURCE, prefix=PREFIX, pins=pins,
        partition='16V100', account='proj-sai-hpc-software', dependency='nep-adapters==1.0.5',
        build_method='Apptainer with a private project directory mounted at the final prefix'))
    image = run / 'neptrainkit-3.1.5.sif'
    quoted = shlex.join
    script = '\n'.join([
        '#!/bin/bash', '#SBATCH --job-name=neptrainkit-3.1.5', '#SBATCH --partition=16V100',
        '#SBATCH --qos=flood-1o2gpu', '#SBATCH --account=proj-sai-hpc-software',
        '#SBATCH --nodes=1', '#SBATCH --ntasks=1',
        '#SBATCH --gpus-per-node=1', '#SBATCH --time=00:30:00', '#SBATCH --export=NIL',
        '#SBATCH --chdir=' + str(run), '#SBATCH --output=' + str(run / 'results/slurm-%j.log'),
        'set -euo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""',
        'export USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?}',
        'export HOME=' + shlex.quote(str(Path.home())),
        'export TMPDIR=' + str(run / 'runtime/tmp'),
        'export APPTAINER_TMPDIR="$TMPDIR" APPTAINER_CACHEDIR=' + str(run / 'runtime/apptainer-cache'),
        'export APPTAINERENV_CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:?}"',
        'nvidia-smi --query-gpu=name,uuid,driver_version --format=csv,noheader > results/gpus.txt',
        'mkdir -p "$TMPDIR" "$APPTAINER_CACHEDIR"',
        quoted(['/usr/bin/python3', '-B', str(run / 'control/prepare.py'), 'check', str(run)]),
        quoted(container(run, BASE, ['/bin/bash', '/control/build.sh'], building=True)),
        quoted(['mksquashfs', str(run / 'rootfs'), str(run / 'final.squashfs'), '-noappend',
                '-all-root', '-no-xattrs', '-processors', '6', '-no-progress']),
        quoted([APP, 'sif', 'new', str(image)]),
        quoted([APP, 'sif', 'add', '--datatype', '4', '--partfs', '1', '--parttype', '2',
                '--partarch', '2', '--groupid', '1', str(image), str(run / 'final.squashfs')]),
        quoted(container(run, image, [PREFIX + '/bin/python', '-B', '/control/science.py',
                '/runtime/tests/data/nep', '/runtime/sif-science.json'])),
        quoted(container(run, image, [PREFIX + '/bin/python', '-B', '/control/gui-smoke.py',
                '/runtime/sif-gui.json'])),
        quoted(['/usr/bin/python3', '-B', str(run / 'control/prepare.py'), 'finalize', str(run)]),
        'echo NEPTRAINKIT_SIF_ACCEPTED', ''])
    (run / 'job.sbatch').write_text(script)
    subprocess.run(['bash', '-n', str(run / 'job.sbatch')], check=True)
    job = subprocess.check_output(['sbatch', '--parsable', '--hold', str(run / 'job.sbatch')], text=True).strip().split(';')[0]
    assert job.isdigit()
    save(run / 'submission.json', dict(job=job, request_sha256=digest(run / 'request.json'), script_sha256=digest(run / 'job.sbatch')))
    subprocess.run(['scontrol', 'release', job], check=True)
    print(json.dumps(dict(job=job, run=str(run)), indent=2))


def check(run):
    request = json.loads((run / 'request.json').read_text())
    submission = json.loads((run / 'submission.json').read_text())
    assert digest(run / 'request.json') == submission['request_sha256']
    assert digest(run / 'job.sbatch') == submission['script_sha256']
    for name, expected in request['pins'].items():
        assert digest(name) == expected, name
    if 'origin' in request:
        root = Path(request['origin']) / 'rootfs' / PREFIX.lstrip('/')
        observed = {str(p.relative_to(root)): dict(kind='link' if p.is_symlink() else 'file',
                    value=os.readlink(p) if p.is_symlink() else digest(p))
                    for p in root.rglob('*') if not p.is_dir()}
        assert observed == request['native_tree'], 'compiled payload changed'
    assert request['source'] == SOURCE and request['prefix'] == PREFIX


def finalize(run):
    check(run)
    import xml.etree.ElementTree as ET
    tests = ET.parse(run / 'runtime/upstream-tests.xml')
    suites = tests.getroot().findall('testsuite')
    assert suites and sum(int(s.attrib['tests']) for s in suites) > 0
    assert all(int(s.attrib['errors']) == int(s.attrib['failures']) == int(s.attrib['skipped']) == 0 for s in suites)
    reports = {}
    for name in ('build-science.json', 'sif-science.json', 'gui-smoke.json', 'sif-gui.json'):
        reports[name] = json.loads((run / 'runtime' / name).read_text())
        assert reports[name]['passed'] is True
    image = run / 'neptrainkit-3.1.5.sif'
    image.chmod(0o444)
    save(run / 'results/acceptance.json', dict(passed=True, source=SOURCE, version='3.1.5',
        image=str(image), image_bytes=image.stat().st_size, image_sha256=digest(image),
        job=os.environ['SLURM_JOB_ID'], partition=os.environ['SLURM_JOB_PARTITION'],
        tests=sum(int(s.attrib['tests']) for s in suites), reports=reports,
        request_sha256=digest(run / 'request.json')))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('submit', 'check', 'finalize'))
    parser.add_argument('run', nargs='?', type=Path)
    args = parser.parse_args()
    if args.operation == 'submit':
        submit()
    else:
        assert args.run and args.run.parent == ROOT / 'runs'
        globals()[args.operation](args.run)
