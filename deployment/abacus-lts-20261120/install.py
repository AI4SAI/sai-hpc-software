#!/usr/bin/env python3
"""Install the fixed ABACUS LTS snapshot from verified SIF bytes.

Default: read-only preflight. The public application receives only executable,
shared runtime libraries and licenses. Evidence stays in this delivery package.
"""
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess

PACKAGE = Path(__file__).resolve().parent
ORIGIN = Path('/org/org-open-sai/proj-sai-hpc-software')
SOURCE = '0d967f1f1bc39c16e3bf435e5f02281c72daaf05'
VERSION = 'LTS-2026-11-16'
MODULE = Path('/opt/modules/modulefiles/apps/abacus/LTS-20261120-auto')
PARTITIONS = ('DSPRHBM', '16V100', '8V100V0', '4V100')
IMAGES = {
    'DSPRHBM': '5841a2d321fa472e5c59a8a6ad73e0306decce364a0adadb98c03a0e7aabef0c',
    '16V100': '6cf6f60dcb6cd87637bf6bca3eeed9b387974dc15960f2a80f36b1c3d3f40cc6',
    '8V100V0': '13750a6bd8ce2f5459ded723fe4f33e1b447b8f9b4c2b4ddca2b02c5c17c5693',
    '4V100': '6a138cc5bb01f5b17e04aaed9316050f4475a9cf8f370a121bf7e74e4b67e636',
}
RECIPES = {
    'cpu': '95113744402d4d616ea28d0c453ac210eff95ead3f3d49bef6b51e1a966d4b16',
    'gpu': 'f650f458490697e2c83a0caefbb4f8972e79e311df25b0bb11e99481d45650b9',
}
MATH = '/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/25.7/math_libs/12.9/targets/x86_64-linux/lib'
POLICY = 'abacus-lts-standard-runtime-v1'


def require(condition, message):
    if not condition:
        raise ValueError(message)

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def plain(path):
    require(path.is_absolute() and '..' not in path.parts, 'noncanonical path: ' + str(path))
    for part in [*reversed(path.parents), path]:
        require(not part.is_symlink(), 'symlink in installation path: ' + str(part))

def pinned(path, expected):
    plain(path)
    require(path.is_file() and path.stat().st_size < 64 * 1024**2, 'missing/oversized metadata: ' + str(path))
    require(digest(path) == expected, 'metadata changed: ' + str(path))
    return json.loads(path.read_text())

def verify_records(root, expected, *, subset=False):
    plain(root)
    seen, pending, files, size = set(), ([root / name for name in expected] if subset else [root]), 0, 0
    while pending:
        path = pending.pop()
        name = str(path.relative_to(root))
        row = expected.get(name)
        require(row is not None, 'unlisted file: ' + str(path))
        info = path.lstat()
        kind = ('symlink' if stat.S_ISLNK(info.st_mode) else 'directory' if stat.S_ISDIR(info.st_mode)
                else 'file' if stat.S_ISREG(info.st_mode) else 'special')
        require(kind == row['kind'], 'file type changed: ' + str(path))
        if kind == 'symlink':
            require(os.readlink(path) == row['target'], 'link target changed: ' + str(path))
        else:
            require(stat.S_IMODE(info.st_mode) == row['mode'], 'permissions changed: ' + str(path))
            if kind == 'directory':
                if not subset:
                    pending.extend(path.iterdir())
            else:
                require(('size' not in row or info.st_size == row['size']) and digest(path) == row['sha256'],
                        'payload changed: ' + str(path))
                files += 1
                size += info.st_size
        seen.add(name)
    require(seen == set(expected), 'missing installed files: ' + str(root))
    return dict(files=files, bytes=size)

def directories(path):
    plain(path)
    for parent in [*reversed(path.parents), path]:
        if not parent.exists():
            parent.mkdir(mode=0o755)
            parent.chmod(0o755)
        require(parent.is_dir(), 'installation ancestor is not a directory')

def image_offset(image):
    listing = subprocess.check_output(['/opt/devtools/apptainer/1.4.4/bin/apptainer',
                                       'sif', 'list', str(image)], text=True)
    partitions = re.findall(r'^\s*\d+\s*\|[^\n|]*\|[^\n|]*\|\s*(\d+)-(\d+)\s*\|\s*FS \(Squashfs/\*System/[^)]+\)\s*$',
                            listing, re.MULTILINE)
    require(len(partitions) == 1, 'expected one primary Squashfs partition')
    start, end = map(int, partitions[0])
    require(0 <= start < end <= image.stat().st_size, 'invalid SIF partition boundaries')
    return start

def extract_files(image, files, destination):
    # Only read file contents. unsquashfs never creates paths or executes code.
    command = ['unsquashfs', '-processors', '2', '-no-progress', '-no-wildcards',
               '-o', str(image_offset(image)), '-cat', str(image), *[row['source'] for row in files]]
    with subprocess.Popen(command, stdout=subprocess.PIPE) as process:
        try:
            for record in files:
                path = destination / record['path']
                directories(path.parent)
                remaining, checksum = record['size'], hashlib.sha256()
                with path.open('xb') as stream:
                    while remaining:
                        block = process.stdout.read(min(remaining, 1024 * 1024))
                        require(block, 'truncated SIF payload: ' + record['path'])
                        stream.write(block); checksum.update(block); remaining -= len(block)
                require(checksum.hexdigest() == record['sha256'], 'SIF file changed: ' + record['path'])
                path.chmod(record['mode'])
            require(not process.stdout.read(1) and process.wait() == 0, 'unexpected SIF extraction result')
        except BaseException:
            process.kill()
            raise


def package_file(name):
    require(isinstance(name, str) and name and not Path(name).is_absolute()
            and '..' not in Path(name).parts, 'unsafe package path')
    path = PACKAGE / name
    plain(path)
    return path


def runtime_file(name):
    """Only the installed program, private shared libraries and license files."""
    return (name == 'bin/abacus' or
            name in ('dependencies/libtorch/build-hash', 'dependencies/libtorch/build-version') or
            bool(re.fullmatch(r'dependencies/libtorch/lib/lib[A-Za-z0-9_.+-]+\.so(?:\.[0-9]+)*', name))
            and Path(name).name not in ('libjitbackend_test.so', 'libtorchbind_test.so') or
            bool(re.fullmatch(r'share/licenses/[A-Za-z0-9_.+-]+/[A-Za-z0-9_.+-]+', name)))


def validate_identity(identity, partition):
    recipe = RECIPES['cpu' if partition == 'DSPRHBM' else 'gpu']
    build = VERSION + '-g' + SOURCE[:12] + '-r' + recipe[:12]
    require(identity['software'] == 'abacus' and identity['track'] == 'development'
            and identity['source_ref'] == 'LTS' and identity['source_sha'] == SOURCE
            and identity['source_version'] == VERSION and identity['partition'] == partition
            and identity['recipe_sha256'] == recipe and identity['build_id'] == build
            and identity['install_prefix'] == '/opt/apps/abacus/abacus-development-' + build + '/' + partition,
            'wrong LTS identity or original installation prefix')


def load_release():
    release = json.loads((PACKAGE / 'release.json').read_text())
    require(release['schema'] == 1 and release['version'] == VERSION
            and release['source_sha'] == SOURCE, 'wrong LTS snapshot')
    require(len(release['deliveries']) == 4 and
            {row['partition'] for row in release['deliveries']} == set(PARTITIONS),
            'expected four partition deliveries')
    require(digest(PACKAGE / MODULE.name) == release['module_sha256'], 'public module changed')
    for row in release['deliveries']:
        validate_identity(row['identity'], row['partition'])
        require(row['image_sha256'] == IMAGES[row['partition']], 'wrong pinned image')
        package_file(row['image'])
        for evidence in row['evidence'].values():
            package_file(evidence['path'])
    return release


def runtime_plan(release):
    plan = json.loads((PACKAGE / 'runtime-plan.json').read_text())
    require(plan['schema'] == 1 and plan['policy'] == POLICY
            and plan['release_sha256'] == digest(PACKAGE / 'release.json'), 'wrong runtime plan')
    require(len(plan['installations']) == 4, 'incomplete runtime plan')
    indexed = {row['identity']['partition']: row for row in plan['installations']}
    require(set(indexed) == set(PARTITIONS), 'duplicate or missing runtime partition')
    for delivery in release['deliveries']:
        entry = indexed[delivery['partition']]
        require(entry['identity'] == delivery['identity'], 'runtime identity differs')
        rows = entry['files']
        paths = {row['path'] for row in rows}
        require(len(paths) == len(rows) and {'.', 'bin', 'bin/abacus'} <= paths, 'invalid runtime inventory')
        for row in rows:
            name = row['path']
            require(name == '.' or (re.fullmatch(r'[A-Za-z0-9_.+/-]+', name)
                    and not Path(name).is_absolute() and
                    all(part not in ('.', '..') for part in name.split('/'))), 'unsafe runtime path')
            require(row['kind'] in ('file', 'directory'), 'unexpected runtime entry type')
            require(row['mode'] in (0o644, 0o755), 'unexpected runtime permissions')
            if row['kind'] == 'file':
                require(runtime_file(name) and type(row['size']) is int and row['size'] >= 0
                        and re.fullmatch(r'[0-9a-f]{64}', row['sha256']), 'non-runtime or malformed file')
            else:
                require(row['mode'] == 0o755 and
                        any(item['kind'] == 'file' and Path(item['path']).is_relative_to(name)
                            for item in rows), 'unnecessary runtime directory')
            if name != '.':
                require(str(Path(name).parent) in paths, 'missing runtime parent')
    return indexed


def verify_evidence(delivery):
    records = {key: pinned(package_file(value['path']), value['sha256'])
               for key, value in delivery['evidence'].items() if key in ('build', 'science', 'accounting')}
    build, science, accounting = (records[key] for key in ('build', 'science', 'accounting'))
    identity = delivery['identity']
    require(build['build_verified'] is True and build['identity'] == identity
            and build['sha256'] == delivery['image_sha256'] and build['artifact'] == delivery['origin_image'],
            'build evidence differs')
    require(science['passed'] is True and science['ranks'] == 1 and science['identity'] == identity
            and science['artifact_sha256'] == delivery['image_sha256']
            and science['artifact'] == delivery['origin_image'], 'scientific evidence differs')
    require(accounting['build']['job'] == delivery['build_job']
            and accounting['science']['job'] == science['job']
            and all(row['state'] == 'COMPLETED' and row['exit_code'] == '0:0'
                    for row in accounting.values()), 'unsuccessful build or scientific Slurm job')
    cases = {'pw', 'hse', 'deepks'} | (set() if delivery['partition'] == 'DSPRHBM' else {'cusolvermp'})
    require(len(science['results']) == len(cases)
            and {row['case'] for row in science['results']} == cases, 'incomplete scientific checks')
    for row in science['results']:
        name = row['case']
        tolerance = 1e-4 if name in ('hse', 'deepks') else 1e-5
        expected_device = 'gpu' if delivery['partition'] != 'DSPRHBM' and name in ('pw', 'cusolvermp') else 'cpu'
        require(row['passed'] is True and row['converged'] is True
                and row['device'] == expected_device and row['energy_tolerance_ev'] == tolerance
                and math.isfinite(row['energy_ev']) and math.isfinite(row['reference_energy_ev'])
                and abs(row['energy_ev'] - row['reference_energy_ev']) <= tolerance,
                'scientific case failed: ' + name)
        receipt = delivery['evidence'][name]
        log = package_file(receipt['path'])
        require(digest(log) == receipt['sha256'], 'raw calculation log changed')
        text = log.read_text()
        energies = re.findall(r'!FINAL_ETOT_IS\s+([-+0-9.eE]+)', text)
        require(energies and float(energies[-1]) == row['energy_ev']
                and 'charge density convergence is achieved' in text.lower(), 'raw SCF result differs')
        if expected_device == 'gpu':
            require(re.search(r'^\s*RUNNING WITH DEVICE\s*:\s*GPU\s*/\s*\S+', text, re.M),
                    'missing GPU execution')
        if name == 'cusolvermp':
            require(re.search(r'^\s*DiagoCusolverMP\s+Diag_CusolverMP_gvd\s', text, re.M),
                    'missing cuSOLVERMp execution')


def preflight(release, physical_root=Path('/'), *, installed=False):
    plan = runtime_plan(release)
    for delivery in release['deliveries']:
        partition = delivery['partition']
        print(partition + ': checking ' + ('installed files' if installed else 'accepted LTS SIF'), flush=True)
        verify_evidence(delivery)
        entry = plan[partition]
        target = physical_root / Path(delivery['identity']['install_prefix']).relative_to('/')
        plain(target)
        expected = {row['path']: row for row in entry['files']}
        if not installed:
            image = package_file(delivery['image'])
            require(image.is_file() and digest(image) == delivery['image_sha256'], 'SIF changed')
        if installed or target.exists():
            verify_records(target, expected)
        print('  %d files, %.2f MiB' % (sum(row['kind'] == 'file' for row in entry['files']),
              sum(row.get('size', 0) for row in entry['files']) / 1024**2), flush=True)
    module = physical_root / MODULE.relative_to('/')
    plain(module)
    if installed or module.exists():
        require(module.is_file() and stat.S_IMODE(module.stat().st_mode) == 0o644
                and module.read_bytes() == (PACKAGE / MODULE.name).read_bytes(), 'existing module differs')


def install(release, physical_root=Path('/')):
    preflight(release, physical_root)
    plan = runtime_plan(release)
    app_root = physical_root / 'opt/apps/abacus'
    directories(app_root)
    descriptor = os.open(app_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        for delivery in release['deliveries']:
            prefix = delivery['identity']['install_prefix']
            target = physical_root / Path(prefix).relative_to('/')
            rows = plan[delivery['partition']]['files']
            expected = {row['path']: row for row in rows}
            plain(target)
            if target.exists():
                verify_records(target, expected)
                continue
            directories(target.parent)
            stage = target.with_name('.' + target.name + '.incoming-' + str(os.getpid()))
            require(not stage.exists() and not stage.is_symlink(), 'staging path exists')
            stage.mkdir(mode=0o755)
            for row in rows:
                if row['kind'] == 'directory' and row['path'] != '.':
                    (stage / row['path']).mkdir(mode=0o755)
            files = [dict(row, source=prefix.lstrip('/') + '/' + row['path'])
                     for row in rows if row['kind'] == 'file']
            extract_files(package_file(delivery['image']), files, stage)
            for row in reversed(rows):
                if row['kind'] == 'directory':
                    (stage / row['path']).chmod(row['mode'])
            verify_records(stage, expected)
            require(not target.exists() and not target.is_symlink(), 'destination appeared during copying')
            stage.rename(target)
            print('Installed ' + str(target), flush=True)
        module = physical_root / MODULE.relative_to('/')
        directories(module.parent)
        if not module.exists():
            stage = module.with_name('.' + module.name + '.incoming-' + str(os.getpid()))
            with stage.open('xb') as output:
                output.write((PACKAGE / MODULE.name).read_bytes())
                output.flush()
                os.fsync(output.fileno())
            stage.chmod(0o644)
            os.link(stage, module)
            stage.unlink()
        preflight(release, physical_root, installed=True)
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument('--check', action='store_true')
    operation.add_argument('--install', action='store_true')
    operation.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    release = load_release()
    if args.install:
        install(release)
    else:
        preflight(release, installed=args.verify)
    print('ABACUS_LTS_NATIVE_' + ('INSTALLED' if args.install else 'VERIFIED' if args.verify else 'PREFLIGHT_PASSED'))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        raise SystemExit('Installation stopped: ' + str(error)) from error
