#!/usr/bin/env python3
"""Export verified final SIF prefixes into an admin handoff in the SAI home."""
import argparse
import json
import math
from pathlib import Path
import re
import shutil
import subprocess

from install import NAME, checksum, inventory, require


def write_json(path, data):
    with path.open('x') as stream:
        stream.write(json.dumps(data, indent=2, sort_keys=True)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--avx2', type=Path, required=True)
    parser.add_argument('--avx512', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    out = args.output
    require(out.is_absolute() and not out.exists(), 'Use a new absolute handoff directory')
    out.mkdir(parents=True, mode=0o755)
    for name in ['install.py', 'install.sh', 'README.md', NAME]:
        shutil.copyfile(package/name, out/name)
        (out/name).chmod(0o644)
    release = dict(version='2026.02.001', module_sha256=checksum(out/NAME),
                   source_sha256='a379f27f4dbd27b2ee45017afec656d064301e97150c874649bdfd64957b75ed',
                   compatibility_patch_sha256=checksum(package/'cuda-aware-cpu.patch'), builds={})
    for isa in ['avx2', 'avx512']:
        run = getattr(args, isa).resolve(strict=True)
        require((run/'accepted.txt').is_file(), f'Build is not accepted: {run}')
        sif = run/f'elpa-{isa}.sif'
        require(checksum(sif) == (run/'artifact.sha256').read_text().split()[0], 'SIF checksum mismatch')
        logs = sorted((run/'work/verify').glob('r*-g*-s*-c*.log'))
        require(len(logs) == 24, 'Expected 24 numerical test invocations')
        for p in logs:
            require(sum(line.startswith('ELPA_NUMERIC PASS ') for line in p.read_text().splitlines()) == 2,
                    'Expected two successful repeated solves: '+str(p))
        passes = [line for p in logs for line in p.read_text().splitlines() if line.startswith('ELPA_NUMERIC PASS ')]
        require(len(passes) == 48 and (run/'work/verify/passed.txt').is_file(), 'Incomplete numerical acceptance')
        require((run/'work/verify/module-passed.txt').is_file(), 'Module has not passed load/unload checks')
        require((run/'work/verify/module.sha256').read_text().split()[0] == release['module_sha256'],
                'Module differs from the tested file')
        prefix = f'/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-{isa}/nvidia'
        squash = run/'work/handoff.squashfs'
        with squash.open('xb') as stream:
            subprocess.run(['/opt/devtools/apptainer/1.4.4/bin/apptainer','sif','dump','1',str(sif)],stdout=stream,check=True)
        extracted = run/'work/handoff-root'
        require(not extracted.exists(), 'Handoff extraction already exists')
        subprocess.run(['unsquashfs','-no-progress','-no-xattrs','-processors','2','-d',str(extracted),str(squash),prefix.lstrip('/')],check=True)
        payload = out/'payload'/isa
        payload.parent.mkdir(exist_ok=True)
        shutil.copytree(extracted/prefix.lstrip('/'), payload, symlinks=True)
        require(checksum(payload/'share/sai/cuda-aware-cpu.patch') == release['compatibility_patch_sha256'],
                'SIF does not contain the reviewed compatibility patch')
        manifest = inventory(payload)
        write_json(out/f'{isa}-manifest.json', manifest)
        proof = out/'evidence'/isa
        shutil.copytree(run/'work/verify', proof, ignore=shutil.ignore_patterns('smoke', 'fortran-smoke'))
        shutil.copyfile(run/'accepted.txt', proof/'accepted.txt')
        metrics = {name: max(float(re.search(name+r'=([0-9.eE+-]+)', line)[1]) for line in passes)
                   for name in ['spectrum','residual','orthogonality']}
        require(all(math.isfinite(v) and 0 <= v < 1e-10 for v in metrics.values()), 'Numerical acceptance failed')
        release['builds'][isa] = dict(prefix=prefix, sif=str(sif), sif_sha256=checksum(sif),
            job_id=(run/'job.id').read_text().strip(), numeric_passes=len(passes), max_errors=metrics,
            manifest_sha256=checksum(out/f'{isa}-manifest.json'))
    write_json(out/'release.json', release)
    print(out)


if __name__ == '__main__':
    main()
