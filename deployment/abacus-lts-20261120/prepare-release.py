#!/usr/bin/env python3
"""Freeze four verified LTS SIFs, scientific evidence and a minimal inventory."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
import install as deployment

RUNS = {
    'DSPRHBM': ('lts-20261119-c-dsprhbm-0d967f1f1bc3', 4),
    '16V100': ('lts-20261119-d-16v100-avx2-0d967f1f1bc3', 5),
    '8V100V0': ('lts-20261119-d-8v100v0-avx512-0d967f1f1bc3', 5),
    '4V100': ('lts-20261119-d-4v100-avx512-0d967f1f1bc3', 5),
}
require, digest = deployment.require, deployment.digest


def write(path, value):
    deployment.directories(path.parent)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')
    path.chmod(0o444)


def copy(source, target, expected):
    deployment.plain(source)
    deployment.plain(target)
    require(digest(source) == expected, 'source differs: ' + str(source))
    deployment.directories(target.parent)
    if target.exists():
        require(digest(target) == expected, 'existing handoff differs')
        return
    temporary = target.with_name('.' + target.name + '.incoming')
    with source.open('rb') as incoming, temporary.open('xb') as outgoing:
        shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
    require(digest(temporary) == expected, 'handoff copy differs')
    temporary.chmod(0o444)
    temporary.rename(target)


def inventory(image, prefix):
    base = ['unsquashfs', '-processors', '2', '-no-progress', '-no-wildcards',
            '-o', str(deployment.image_offset(image))]
    listing = subprocess.check_output(base + ['-lln', str(image), prefix.lstrip('/')], text=True)
    files = []
    for line in listing.splitlines():
        match = re.fullmatch(r'([-dlrwxstST]{10})\s+\d+/\d+\s+(\d+)\s+\d{4}-\d{2}-\d{2}'
                             r'\s+\d{2}:\d{2}\s+squashfs-root/(.+)', line)
        if not match:
            continue
        mode, size, name = match.groups()
        if not name.startswith(prefix.lstrip('/') + '/'):
            continue
        relative = name.removeprefix(prefix.lstrip('/') + '/')
        if not deployment.runtime_file(relative):
            continue
        require(mode in ('-rwxr-xr-x', '-rw-r--r--'), 'unexpected runtime file type/mode: ' + name)
        files.append(dict(path=relative, kind='file', size=int(size),
                          mode=0o755 if mode == '-rwxr-xr-x' else 0o644))
    files.sort(key=lambda row: row['path'])
    require(len({row['path'] for row in files}) == len(files), 'duplicate runtime path')
    names = {row['path'] for row in files}
    require({'bin/abacus', 'dependencies/libtorch/lib/libc10.so',
             'dependencies/libtorch/lib/libtorch_cpu.so'} <= names, 'missing required runtime')
    with subprocess.Popen(base + ['-cat', str(image)] +
                          [prefix.lstrip('/') + '/' + row['path'] for row in files],
                          stdout=subprocess.PIPE) as process:
        try:
            for row in files:
                remaining, checksum = row['size'], hashlib.sha256()
                while remaining:
                    block = process.stdout.read(min(remaining, 1024 * 1024))
                    require(block, 'truncated runtime inventory')
                    checksum.update(block)
                    remaining -= len(block)
                row['sha256'] = checksum.hexdigest()
            require(not process.stdout.read(1) and process.wait() == 0, 'unexpected inventory payload')
        except BaseException:
            process.kill()
            raise
    parents = {str(parent) for row in files for parent in Path(row['path']).parents}
    return sorted(files + [dict(path=name, kind='directory', mode=0o755) for name in parents],
                  key=lambda row: row['path'])


def accounting(job):
    lines = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', job, '-o',
                                     'JobIDRaw,State,ExitCode,Elapsed,Start,End,NodeList'], text=True)
    rows = [line.split('|') for line in lines.splitlines() if line.split('|')[0] == job]
    require(len(rows) == 1, 'missing Slurm accounting')
    result = dict(zip(('job', 'state', 'exit_code', 'elapsed', 'start', 'end', 'nodes'), rows[0]))
    require(result['state'] == 'COMPLETED' and result['exit_code'] == '0:0', 'Slurm job did not pass')
    return result


def main():
    package, origin = deployment.PACKAGE, deployment.ORIGIN
    require(not (package / 'release.json').exists(), 'snapshot already frozen')
    deliveries, plans = [], []
    for partition, (name, attempt) in RUNS.items():
        build = origin / 'runs' / name
        task = origin / 'runtime-tests' / (name + '-lts-science-r' + str(attempt))
        image = Path((build / 'artifact.path').read_text().strip())
        manifest = image.with_suffix('.json')
        record = json.loads(manifest.read_text())
        identity = record['identity']
        deployment.validate_identity(identity, partition)
        require(record['build_verified'] is True and record['sha256'] == deployment.IMAGES[partition],
                'build is not the accepted image')
        science_path = task / 'results/verification.json'
        science = json.loads(science_path.read_text())
        require(science['passed'] is True and science['artifact_sha256'] == record['sha256']
                and science['identity'] == identity, 'science does not match the image')
        build_job = (build / 'job.id').read_text().strip()
        public_image = package / 'images' / (partition + '.sif')
        copy(image, public_image, record['sha256'])
        evidence = {}
        for key, source in [('build', manifest), ('science', science_path)] + [
                (row['case'], task / row['case'] / 'OUT.autotest/running_scf.log')
                for row in science['results']]:
            destination = package / 'evidence' / partition / (key + source.suffix)
            copy(source, destination, digest(source))
            evidence[key] = dict(path=str(destination.relative_to(package)), sha256=digest(destination))
        path = package / 'evidence' / partition / 'accounting.json'
        write(path, dict(build=accounting(build_job), science=accounting(science['job'])))
        evidence['accounting'] = dict(path=str(path.relative_to(package)), sha256=digest(path))
        delivery = dict(partition=partition, identity=identity, image=str(public_image.relative_to(package)),
                        origin_image=str(image), image_sha256=record['sha256'],
                        build_job=build_job, evidence=evidence)
        deployment.verify_evidence(delivery)
        rows = inventory(public_image, identity['install_prefix'])
        deliveries.append(delivery)
        plans.append(dict(identity=identity, files=rows))
        print(partition + ': pinned image, raw scientific evidence and runtime inventory', flush=True)
    release = dict(schema=1, version=deployment.VERSION, source_sha=deployment.SOURCE,
                   generated_at=datetime.now(timezone.utc).isoformat(),
                   module_sha256=digest(package / deployment.MODULE.name), deliveries=deliveries)
    write(package / 'release.json', release)
    write(package / 'runtime-plan.json', dict(schema=1, policy=deployment.POLICY,
          release_sha256=digest(package / 'release.json'), installations=plans))
    deployment.preflight(deployment.load_release())
    print('LTS administrator package ready.')


if __name__ == '__main__':
    main()
