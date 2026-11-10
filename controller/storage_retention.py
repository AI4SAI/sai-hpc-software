#!/usr/bin/env python3
"""Inventory build storage and prepare a manual, exact-file scratch cleanup.

No arguments remove files. `plan` only writes a review manifest; `check` checks
it. `apply` requires its SHA256 and deletes only the two named MD intermediates.
SIF retention is advisory: never remove images, evidence, caches or modules.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time

PROJECT = Path('/org/org-open-sai/proj-sai-hpc-software')
BETA = Path('/home/stardust/sai-hpc-software/sif-beta')
FAMILIES = {'experimental/deepmd-lammps/runs': 'lammps',
            'experimental/deepmd-standalone/runs': 'deepmd-kit'}
SCRATCH = {'work.ext3', 'final.squashfs'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def plain(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts and path.resolve() == path,
            'redirected/noncanonical path: ' + str(path))
    return path


def digest(path):
    with plain(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(plain(path).read_text())


def info(path):
    details = plain(path).lstat()
    require(stat.S_ISREG(details.st_mode) and details.st_nlink == 1,
            'expected a singly linked regular file: ' + str(path))
    return {key: getattr(details, 'st_' + key) for key in
            ('dev', 'ino', 'size', 'mtime_ns', 'ctime_ns', 'mode', 'nlink', 'blocks')}


def proof(directory, software):
    require(not (directory / '.keep-build').exists() and not (directory / '.keep-build').is_symlink(),
            'manually pinned build')
    request = read(directory / 'request.json')
    status = read(directory / 'results/status.json')
    delivery = request['delivery']
    require(status['delivery'] == delivery and delivery['identity']['software'] == software,
            'build identity differs')
    require(status['build_verified'] is True, 'build was not verified')
    if software == 'deepmd-kit':
        require(status['scientific_verified'] is True, 'DeePMD science was not verified')
        job = request['job_id']
        require(status['job_id'] == job, 'job differs')
    else:
        job = (directory / 'job.id').read_text().strip()
        require(status['job'] == job and status['state'] == 'COMPLETED'
                and status['exit_code'] == '0:0', 'build did not complete')
    require(job.isdigit(), 'invalid build job')
    image = directory / 'candidate.sif'
    image_info = info(image)
    require(not image_info['mode'] & 0o222, 'SIF is not sealed')
    sha = status['artifact_sha256']
    require(len(sha) == 64 and all(c in '0123456789abcdef' for c in sha), 'invalid SIF hash')
    return dict(job=job, image=str(image), image_info=image_info, sha256=sha,
                request_sha256=digest(directory / 'request.json'),
                status_sha256=digest(directory / 'results/status.json'))


def inventory(root):
    """Stat only top-level build files; never walk expanded source/build trees."""
    result = {}
    for family in ('runs', *FAMILIES):
        rows = {}
        base = root / family
        if base.exists():
            for directory in base.iterdir():
                if directory.is_symlink() or not directory.is_dir():
                    continue
                for name in (*sorted(SCRATCH), 'candidate.sif'):
                    path = directory / name
                    if path.is_file() and not path.is_symlink():
                        data = path.stat()
                        row = rows.setdefault(name, dict(files=0, apparent_bytes=0, allocated_bytes=0))
                        row['files'] += 1
                        row['apparent_bytes'] += data.st_size
                        row['allocated_bytes'] += data.st_blocks * 512
        result[family] = rows
    return result


def beta_retention(root, now, keep=3, days=7):
    """Propose older generations for a separate reference-aware review only."""
    rows, errors = [], []
    pins_file = root / 'storage-pins.json'
    pins = read(pins_file) if pins_file.exists() else []
    require(isinstance(pins, list) and all(isinstance(x, str) for x in pins), 'pins must be generation names')
    for path in sorted((root / 'generations').glob('*/manifest.json')):
        try:
            manifest = read(path)
            bundle = plain(Path(manifest['bundle']))
            require(bundle.parent == root / 'runtimes', 'bundle outside runtime directory')
            catalog = read(bundle / 'catalog.json')
            require(digest(bundle / 'catalog.json') == manifest['catalog_sha256'], 'catalog changed')
            parts = [(software, target, record) for software, application in catalog['applications'].items()
                     for target, record in application['partitions'].items()]
            signature = sorted((name, target, row['identity']['source_sha'], row['identity']['recipe_sha256'])
                               for name, target, row in parts)
            channels = sorted({(name, row['identity']['source_ref']) for name, _, row in parts})
            reasons = []
            for software, version in manifest['versions'].items():
                modules = Path(manifest['module_root']) / software
                if any(link.resolve().name == version['name'] for link in modules.glob('*-beta-later')):
                    reasons.append('current beta')
            if any(row['identity']['track'] != 'development' for _, _, row in parts):
                reasons.append('release/prerelease')
            if path.parent.name in pins or (path.parent / '.keep').exists():
                reasons.append('manual pin')
            if now - path.stat().st_mtime < days * 86400:
                reasons.append('minimum age')
            images = {plain(Path(row['image'])) for _, _, row in parts}
            require(all(image.is_relative_to(bundle) for image in images), 'image outside bundle')
            rows.append(dict(generation=path.parent.name, bundle=str(bundle),
                             modified=path.stat().st_mtime, signature=signature, channels=channels,
                             allocated_image_bytes=sum(image.stat().st_blocks * 512 for image in images),
                             retain_reasons=sorted(set(reasons))))
        except (OSError, KeyError, TypeError, ValueError) as error:
            errors.append(dict(manifest=str(path), error=str(error)))
    distinct = {}
    # Retain one representative of each of the latest distinct build sets.
    for row in sorted(rows, key=lambda row: row['modified'], reverse=True):
        channel, signature = json.dumps(row['channels']), json.dumps(row['signature'])
        seen = distinct.setdefault(channel, set())
        if signature not in seen and len(seen) < keep:
            row['retain_reasons'].append('latest distinct build set')
            seen.add(signature)
        row['review_only'] = not row['retain_reasons']
    return dict(keep_distinct=keep, minimum_days=days, automatic_deletion=False,
                generations=rows, errors=errors)


def plan(root, beta, *, now=None, age_hours=24):
    root, beta = plain(root), plain(beta)
    now = time.time() if now is None else now
    rows, skipped = [], []
    for family, software in FAMILIES.items():
        for directory in sorted((root / family).glob('*')):
            if not directory.is_dir() or directory.is_symlink():
                continue
            files = [directory / name for name in sorted(SCRATCH) if (directory / name).exists()]
            if not files:
                continue
            try:
                evidence = proof(directory, software)
                require(now - (directory / 'results/status.json').stat().st_mtime >= age_hours * 3600,
                        'successful status is too recent')
                selected = [dict(path=str(path), info=info(path)) for path in files]
                rows.append(dict(run=str(directory), software=software, proof=evidence, files=selected))
            except (OSError, ValueError, KeyError) as error:
                skipped.append(dict(run=str(directory), reason=str(error)))
    return dict(schema=1, generated_at=datetime.fromtimestamp(now, timezone.utc).isoformat(),
                root=str(root), age_hours=age_hours, inventory=inventory(root),
                candidates=rows, skipped=skipped,
                candidate_allocated_bytes=sum(f['info']['blocks'] * 512 for row in rows for f in row['files']),
                candidate_inodes=sum(len(row['files']) for row in rows),
                beta=beta_retention(beta, now))


def command(argv):
    return subprocess.check_output(argv, text=True)


def idle():
    require(not command(['squeue', '-h', '--me', '-o', '%i']).strip(),
            'active or queued jobs exist; repeat cleanup between runs')


def validate_plan(plan):
    require(plan['schema'] == 1, 'unknown cleanup schema')
    root = plain(Path(plan['root']))
    seen = set()
    for row in plan['candidates']:
        directory = plain(Path(row['run']))
        family = str(directory.parent.relative_to(root))
        require(FAMILIES.get(family) == row['software'] and directory not in seen,
                'unexpected or duplicate build directory')
        seen.add(directory)
        names = [Path(f['path']).name for f in row['files']]
        require(names and len(set(names)) == len(names), 'empty or duplicate cleanup list')
        for item in row['files']:
            path = plain(Path(item['path']))
            require(path.parent == directory and path.name in SCRATCH, 'not a disposable build file')


def check(plan, *, require_idle=True):
    validate_plan(plan)
    if require_idle:
        idle()
    jobs = [row['proof']['job'] for row in plan['candidates']]
    require(all(job.isdigit() for job in jobs), 'invalid job identity')
    accounting = {}
    if jobs:
        for line in command(['sacct', '-X', '-n', '-P', '-j', ','.join(jobs),
                             '-o', 'JobIDRaw,State,ExitCode']).splitlines():
            fields = line.split('|')
            if len(fields) >= 3:
                require(fields[0] not in accounting, 'duplicate job accounting')
                accounting[fields[0]] = fields[1:3]
    for row in plan['candidates']:
        directory = Path(row['run'])
        require(proof(directory, row['software']) == row['proof'], 'build proof changed: ' + str(directory))
        require(accounting.get(row['proof']['job']) == ['COMPLETED', '0:0'], 'job no longer confirms success')
        require(digest(Path(row['proof']['image'])) == row['proof']['sha256'], 'retained SIF changed')
        for item in row['files']:
            require(info(Path(item['path'])) == item['info'], 'scratch file changed: ' + item['path'])
        # Exercise the same directory traversal as apply, without deleting.
        descriptor = open_directory(directory)
        os.close(descriptor)
    if require_idle:
        idle()


def open_directory(path):
    """Anchor deletion below real directories even if a path is renamed."""
    # /org permits search but not directory listing. Linux O_PATH obtains a
    # dir_fd without requiring read access; O_DIRECTORY|O_NOFOLLOW still rejects
    # symlinks, including the last component, and unlinkat checks permissions.
    flags = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open('/', flags)
    try:
        for name in path.parts[1:]:
            next_fd = os.open(name, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_fd
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def apply(plan):
    check(plan)  # Verify the complete plan before the first unlink.
    for row in plan['candidates']:
        idle()
        require(proof(Path(row['run']), row['software']) == row['proof'], 'build changed during cleanup')
        descriptor = open_directory(Path(row['run']))
        try:
            for item in row['files']:
                name = Path(item['path']).name
                details = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                require(all(getattr(details, 'st_' + key) == value for key, value in item['info'].items()),
                        'file changed immediately before unlink')
                os.unlink(name, dir_fd=descriptor)
                print('REMOVED ' + item['path'], flush=True)
        finally:
            os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    operations = parser.add_subparsers(dest='action', required=True)
    planner = operations.add_parser('plan')
    planner.add_argument('output', type=Path)
    planner.add_argument('--root', type=Path, default=PROJECT)
    planner.add_argument('--beta', type=Path, default=BETA)
    planner.add_argument('--age-hours', type=int, default=24)
    for name in ('check', 'apply'):
        action = operations.add_parser(name)
        action.add_argument('manifest', type=Path)
        action.add_argument('--sha256', required=True)
    args = parser.parse_args()
    if args.action == 'plan':
        require(args.age_hours >= 0, 'negative minimum age')
        result = plan(args.root, args.beta, age_hours=args.age_hours)
        with args.output.open('x') as stream:
            json.dump(result, stream, sort_keys=True, indent=2); stream.write('\n')
        print(f"Review: {result['candidate_inodes']} files; {result['candidate_allocated_bytes'] / 1024**3:.2f} GiB")
        print('Manifest SHA256: ' + digest(args.output.resolve()))
    else:
        require(digest(args.manifest.resolve()) == args.sha256, 'reviewed cleanup manifest changed')
        result = read(args.manifest.resolve())
        if args.action == 'apply':
            apply(result)
        else:
            check(result, require_idle=False)
        print('Cleanup complete.' if args.action == 'apply' else 'Read-only check passed; no files removed.')


if __name__ == '__main__':
    main()
