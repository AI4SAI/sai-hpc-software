#!/usr/bin/env python3
"""Publish reviewed Lmod defaults after complete native update1 acceptance."""
import argparse
from datetime import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parent
UPDATE1 = Path('/home/stardust/sai-hpc-software/deployment/lammps-30Sep2026-update1')
ROOT = Path('/opt/modules/modulefiles/apps')
PARTITIONS = {'4V100', '16V100', '8V100V0'}
LAMMPS = 'lammps/30Sep2026_update1-deepmd320-auto'
NEPADAPTERS = 'nepadapters/1.0.5-lammps30Sep2026_update1'
PLUGIN = Path('/opt/apps/nepadapters/1.0.5-lammps30Sep2026')
TARGETS = {LAMMPS, NEPADAPTERS, 'lammps/.modulerc.lua', 'nepadapters/.modulerc.lua'}


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def plain(path):
    require(path.is_absolute() and '..' not in path.parts, 'noncanonical path: ' + str(path))
    require(all(not p.is_symlink() for p in (path, *path.parents)), 'symlink in path: ' + str(path))


def relative(name):
    path = Path(name)
    require(name and not path.is_absolute() and '..' not in path.parts and str(path) == name,
            'invalid package path: ' + name)
    return path


def checked(path, expected):
    plain(path)
    require(path.is_file() and digest(path) == expected, 'missing or changed file: ' + str(path))
    return path


def load_manifest():
    path = PACKAGE / 'manifest.json'
    require(path.is_file(), 'Complete three-partition native acceptance is not yet packaged; defaults remain unchanged.')
    manifest = json.loads(path.read_text())
    require(manifest['schema'] == 1 and manifest['ready'] is True
            and set(manifest['partitions']) == PARTITIONS, 'incomplete release qualification')
    require(set(manifest['modules']) == TARGETS, 'unexpected module destinations')
    for name, sha in manifest['files'].items():
        checked(PACKAGE / relative(name), sha)
    for name, sha in manifest['update1_package_files'].items():
        checked(UPDATE1 / relative(name), sha)
    return manifest


def update1_module(manifest):
    # Verify all executable helper bytes before importing the existing installer.
    for name in ('install.py', 'runtime_files.py', 'release.json', LAMMPS.split('/')[1]):
        checked(UPDATE1 / name, manifest['update1_package_files'][name])
    sys.path.insert(0, str(UPDATE1))
    spec = importlib.util.spec_from_file_location('reviewed_update1_install', UPDATE1 / 'install.py')
    deployment = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(deployment)
    sys.path.pop(0)
    return deployment


def evidence(manifest, release):
    verified = json.loads((PACKAGE / 'evidence/lammps-verification.json').read_text())
    require(verified['passed'] is True and len(verified['results']) == 3
            and {r['partition'] for r in verified['results']} == PARTITIONS, 'incomplete native LAMMPS matrix')
    rows = {r['partition']: r for r in release['deliveries']}
    for result in verified['results']:
        part = result['partition']
        report_path = PACKAGE / ('evidence/lammps-' + part + '.json')
        report = json.loads(report_path.read_text())
        require(result['passed'] is True and result['route'] == report['route'] == 'physical-opt'
                and result['cases'] == len(report['samples']) == 14
                and result['job'] == report['job'] and report['partition'] == part
                and result['acceptance_sha256'] == digest(report_path)
                and report['prefix'] == rows[part]['prefix'], 'native LAMMPS proof mismatch')
        request = json.loads((PACKAGE / ('evidence/lammps-' + part + '-request.json')).read_text())
        require(request['spec']['identity'] == rows[part]['identity']
                and request['spec']['files'] == rows[part]['files']
                and report['request_sha256'] == digest(PACKAGE / ('evidence/lammps-' + part + '-request.json')),
                'accepted LAMMPS bytes differ')
        plugin = json.loads((PACKAGE / ('evidence/nepadapters-' + part + '.json')).read_text())
        request = json.loads((PACKAGE / ('evidence/nepadapters-' + part + '-request.json')).read_text())
        expected = {Path(k).name: v['sha256'] for k, v in manifest['plugin']['files'].items() if k.startswith('lib/')}
        require(plugin['passed'] is True and plugin['installed'] is True and plugin['partition'] == part
                and set(plugin['phases']) == {'local', 'distributed'}
                and plugin['plugin_sha256'] == request['files'] == expected
                and request['plugins'] == str(PLUGIN / 'lib')
                and request['request']['lammps']['identity'] == rows[part]['identity']
                and request['nepadapters_module'] == NEPADAPTERS
                and request['pins']['modulefiles/' + NEPADAPTERS] == manifest['modules'][NEPADAPTERS]['sha256'],
                'NEPAdapters proof mismatch')
        for phase, record in plugin['phases'].items():
            modes = {'single', 'multi-gpu'} if phase == 'local' else {'multi-node'}
            require(record['passed'] is True and set(record['proofs']) == modes
                    and record['job'] == plugin['jobs'][phase]
                    and record['request_sha256'] == digest(PACKAGE / ('evidence/nepadapters-' + part + '-request.json')),
                    'incomplete NEP execution matrix')
            for sample in record['proofs'].values():
                require(sample['lmp'] == rows[part]['prefix'] + '/bin/lmp'
                        and sample['plugin_sha256'] == expected
                        and sample['passed'] is True and sample['gpu_dynamics']['Step'] == 100
                        and set(sample['boundary_cases']) == {'p p f', 'f f f'}, 'incomplete NEP scientific cases')
        accounting = manifest['nepadapters_accounting'][part]
        require(set(accounting) == {'local', 'distributed'}, 'missing Slurm accounting')
        for phase, record in accounting.items():
            require(record[:5] == [plugin['jobs'][phase], 'COMPLETED', '0:0', part, 'proj-sai-hpc-software']
                    and record[5] == manifest['nepadapters_work'] + '/' + part,
                    'NEP job not successfully completed in the requested allocation')


def module_sources(manifest):
    result = {}
    for name, row in manifest['modules'].items():
        source = UPDATE1 / LAMMPS.split('/')[1] if name == LAMMPS else PACKAGE / 'modulefiles' / name
        checked(source, row['sha256'])
        result[name] = source.read_bytes()
    return result


def check_targets(manifest, target_root, *, installed=False):
    plain(target_root)
    for family in ('lammps', 'nepadapters'):
        for name in ('.modulerc', '.version', 'default'):
            path = target_root / family / name
            require(not path.exists() and not path.is_symlink(), 'another default mechanism exists: ' + str(path))
    for name, row in manifest['modules'].items():
        path = target_root / name
        plain(path)
        if not path.exists():
            require(not installed and (name != LAMMPS or target_root != ROOT), 'missing installed module: ' + str(path))
            continue
        info = path.stat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o644,
                'unexpected module file type or mode: ' + str(path))
        accepted = [row['sha256']] if installed else [row['sha256'], *row.get('previous_sha256', [])]
        require(digest(path) in accepted, 'existing module differs: ' + str(path))


def preflight(manifest, target_root=ROOT, *, installed=False):
    deployment = update1_module(manifest)
    release = deployment.load_release()
    deployment.preflight(release, installed=True, allow_previous_module=not installed)
    evidence(manifest, release)
    require(manifest['plugin']['prefix'] == str(PLUGIN), 'wrong shared plugin')
    for name, row in manifest['plugin']['files'].items():
        path = checked(PLUGIN / relative(name), row['sha256'])
        require(path.stat().st_size == row['size'], 'plugin size changed')
    module_sources(manifest)
    check_targets(manifest, target_root, installed=installed)


def publish(manifest, target_root=ROOT):
    check_targets(manifest, target_root)
    sources = module_sources(manifest)
    target_root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        check_targets(manifest, target_root)
        # Publish the compatible module and help before either default changes.
        order = (NEPADAPTERS, LAMMPS, 'lammps/.modulerc.lua', 'nepadapters/.modulerc.lua')
        for name in order:
            target = target_root / name
            content = sources[name]
            if target.exists() and target.read_bytes() == content:
                continue
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            previous = (target.stat(), digest(target)) if target.exists() else None
            if previous:
                backup = (Path.home() / 'sai-hpc-module-backups' if target_root == ROOT else target_root / '.backups')
                backup = backup / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid())) / name
                backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with backup.open('xb') as stream:
                    stream.write(target.read_bytes())
                print('Previous module: ' + str(backup), flush=True)
            stage = target.with_name('.' + target.name + '.incoming-' + str(os.getpid()))
            with stage.open('xb') as stream:
                stream.write(content); stream.flush(); os.fsync(stream.fileno())
            stage.chmod(0o644)
            if previous:
                require(digest(target) == previous[1], 'module changed during publication')
                os.chown(stage, previous[0].st_uid, previous[0].st_gid)
                os.replace(stage, target)
            else:
                os.link(stage, target)
                stage.unlink()
            print('Published ' + str(target), flush=True)
        check_targets(manifest, target_root, installed=True)
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    for name in ('check', 'install', 'verify'):
        actions.add_argument('--' + name, action='store_true')
    parser.add_argument('--stage-root', type=Path, help='trial module tree below project operations')
    args = parser.parse_args()
    target_root = ROOT
    if args.stage_root:
        plain(args.stage_root)
        require(args.stage_root.is_relative_to('/org/org-open-sai/proj-sai-hpc-software/operations'), 'invalid trial root')
        target_root = args.stage_root / ROOT.relative_to('/')
    manifest = load_manifest()
    preflight(manifest, target_root, installed=args.verify and not args.stage_root)
    if args.install:
        publish(manifest, target_root)
    if args.verify:
        check_targets(manifest, target_root, installed=True)
    print('LAMMPS_UPDATE1_DEFAULT_' + ('INSTALLED' if args.install else 'VERIFIED' if args.verify else 'PREFLIGHT_PASSED'))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit('Default publication stopped: ' + str(error)) from error
