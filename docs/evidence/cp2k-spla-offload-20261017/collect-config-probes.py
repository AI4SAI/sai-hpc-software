#!/usr/bin/env python3
"""Read retained config-probe files; never mount or modify any image."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


def evidence(path):
    data = path.read_bytes()
    return dict(path=str(path), sha256=hashlib.sha256(data).hexdigest(),
                text=data.decode('utf-8'))


def collect(project):
    records = []
    for suffix in ('c', 'd'):
        run = project / 'runs' / ('cp2k-spack-config-probe-20261017-' + suffix)
        job = (run / 'job.id').read_text().strip()
        if not job.isdigit():
            raise ValueError('invalid job id')
        files = ['job.id', 'input.sha256', 'results/slurm-' + job + '.log',
                 'results/image-before.sha256', 'results/image-driver.json',
                 'config-build/CMakeCache.txt', 'build-env.sh']
        if suffix == 'd':
            files += ['results/image-after.sha256', 'results/config.exit',
                      'results/prior-c-integrity.log']
        record = dict(run=str(run), job_id=job, files={
            name: evidence(run / name) for name in files},
            inputs={str(p.relative_to(run)): evidence(p)
                    for p in sorted((run / 'input').rglob('*')) if p.is_file()},
            accounting=subprocess.check_output(
                ['sacct', '-X', '-nP', '-j', job, '-o',
                 'JobIDRaw,Partition,State,Start,Elapsed,AllocCPUS,NodeList,ExitCode'],
                text=True))
        records.append(record)
    c, d = records
    current = d['files']
    before = current['results/image-before.sha256']['text']
    after = current['results/image-after.sha256']['text']
    log = current['results/slurm-' + d['job_id'] + '.log']['text']
    passed = (before == after == c['files']['results/image-before.sha256']['text']
              and current['results/config.exit']['text'].strip() == '0'
              and 'CP2K_PRIVATE_COSMA_SPLA_CONFIGS_VERIFIED' in log
              and 'READONLY_IMAGE_UNCHANGED' in log
              and '|COMPLETED|' in d['accounting']
              and d['accounting'].rstrip().endswith('|0:0'))
    return dict(schema=1, checked_at=datetime.now(timezone.utc).isoformat(),
                status='passed' if passed else 'failed', probes=records,
                scope='real installed COSMA/COSTA/tiled-mm and SPLA CMake config lookup',
                limitations=[
                    'The diagnostic dependency image already differs from its original release seal.',
                    'No release checkpoint is resealed, relabelled or accepted by this probe.',
                    'C failed because its fixture requested lowercase spla instead of CP2K upstream SPLA.',
                    'C image preservation is checked by D before any subsequent mount.',
                    'This configure probe does not compile or scientifically validate CP2K.'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    project = Path('/home/stardust/sai-hpc-software')
    if not args.output.resolve().is_relative_to(project / 'diagnostics'):
        raise ValueError('evidence must remain in project home diagnostics')
    result = collect(project)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write('\n')
    print(json.dumps(dict(output=str(args.output), status=result['status'])))
    if result['status'] != 'passed':
        raise SystemExit(1)
