#!/usr/bin/env python3
"""Archive bounded recovery/science/diagnostic evidence with per-file hashes."""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

from submit import ROOT, TARGETS, name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('label')
    args = parser.parse_args()
    if not args.label.replace('-', '').isalnum():
        raise ValueError('unsafe snapshot label')
    files, sources, jobs = {}, {}, set()

    def add(path, relative):
        if not path.is_file() or path.is_symlink():
            return
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('unexpected large evidence file: ' + str(path))
        files[relative] = path.read_bytes()
        sources[relative] = str(path)
        if path.name == 'job.id':
            jobs.add(files[relative].decode().strip())

    def science_tree(directory, destination):
        if not directory.exists():
            return
        names = {'request.json', 'job.id', 'job.sbatch', 'rank-exec.sh', 'cp2k-phase',
                 'cp2k-candidate', 'cp2k.log', 'input.inp', 'forces.xyz', 'elapsed.txt',
                 'BASIS_MOLOPT', 'GTH_POTENTIALS', 'binary.txt', 'binary.sha256',
                 'mpirun.txt', 'map.txt', 'candidate-ldd.txt'}
        for p in directory.rglob('*'):
            if (p.is_file() and (p.name in names or 'results' in p.relative_to(directory).parts
                    or 'resources' in p.relative_to(directory).parts
                    or 'ranks' in p.relative_to(directory).parts
                    or p.name.startswith('elpa-library.'))):
                add(p, destination + '/' + str(p.relative_to(directory)))

    for target in TARGETS:
        build = ROOT / 'runs' / (name(target) + '-export')
        for p in build.iterdir():
            if p.suffix in ('.json', '.sbatch') or p.name in ('job.id', 'artifact.path'):
                add(p, 'build/' + target + '/' + p.name)
        for folder in ('results', 'recovery-attempts'):
            for p in (build / folder).rglob('*'):
                add(p, 'build/' + target + '/' + str(p.relative_to(build)))
        if (build / 'artifact.path').exists():
            artifact = Path((build / 'artifact.path').read_text().strip())
            add(artifact.with_suffix('.json'), 'build/' + target + '/artifact-manifest.json')
        science_tree(ROOT / 'runtime-tests' / (build.name + '-science'), 'science/' + target)
        science_tree(ROOT / 'runtime-tests' / ('cp2k-elpa-diagnostic-20261020-' + target), 'diagnostic/' + target)
        science_tree(ROOT / 'runtime-tests' / ('cp2k-elpa-host-staging-20261020-' + target), 'candidate-science/' + target)
    science_tree(ROOT / 'runtime-tests/cp2k-elpa-phases-20261020-4v100-avx512', 'phase-diagnostic')
    for isa in ('avx2', 'avx512'):
        candidate = ROOT / 'runs' / ('elpa-2026.02.001-backtransform-20261020-' + isa)
        if not candidate.exists():
            continue
        for p in candidate.iterdir():
            if p.suffix in ('.json', '.out', '.txt', '.sha256') or p.name == 'job.id':
                add(p, 'elpa-candidate/' + isa + '/' + p.name)
        for folder in ('control', 'work/verify'):
            for p in (candidate / folder).rglob('*'):
                if p.suffix in ('.sh', '.sbatch', '.c', '.py', '.patch', '.log', '.txt'):
                    add(p, 'elpa-candidate/' + isa + '/' + str(p.relative_to(candidate)))
    accounting = subprocess.check_output(['sacct', '-X', '-nP', '-j', ','.join(sorted(jobs)),
        '-o', 'JobIDRaw,Partition,State,ExitCode,Elapsed,NodeList'], text=True)
    files['accounting.txt'] = accounting.encode()
    files['observed.json'] = (json.dumps(dict(observed_utc=datetime.now(timezone.utc).isoformat(),
        note='Timestamped snapshot; only explicit completed verification establishes acceptance.'), indent=2) + '\n').encode()
    files['sources.json'] = (json.dumps(sources, indent=2, sort_keys=True) + '\n').encode()
    files['evidence-sha256.json'] = (json.dumps({k: hashlib.sha256(v).hexdigest() for k, v in files.items()},
                                             indent=2, sort_keys=True) + '\n').encode()
    path = ROOT / 'diagnostics' / ('cp2k-export-' + args.label + '.tgz')
    with path.open('xb') as stream, tarfile.open(fileobj=stream, mode='w:gz') as archive:
        for relative, content in sorted(files.items()):
            info = tarfile.TarInfo(relative)
            info.size, info.mode = len(content), 0o644
            archive.addfile(info, io.BytesIO(content))
    print(json.dumps(dict(path=str(path), files=len(files), bytes=path.stat().st_size,
                          sha256=hashlib.sha256(path.read_bytes()).hexdigest())))


if __name__ == '__main__':
    main()
