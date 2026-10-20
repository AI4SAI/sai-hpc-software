#!/usr/bin/env python3
"""Build an isolated ELPA workaround SIF at the final container prefix."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from submit import ROOT, execute


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('isa', choices=('avx2', 'avx512'), nargs='?', default='avx512')
    isa = parser.parse_args().isa
    package = ROOT / 'development/workflow-closure/deployment/elpa-2026.02.001'
    run = ROOT / 'runs' / ('elpa-2026.02.001-backtransform-20261020-' + isa)
    run.mkdir(mode=0o700)
    for directory in ('control', 'input/reference', 'install', 'work', 'runtime', 'cache'):
        (run / directory).mkdir(parents=True)
    filenames = ('build.sh', 'build.sbatch', 'container.sh', 'environment.sh', 'smoke.c',
                 'verify.sh', 'cuda-aware-cpu.patch', 'mpi-communicator.patch', 'comm-trace.c',
                 'verify-lifecycle.py')
    for filename in filenames:
        shutil.copyfile(package / filename, run / 'control' / filename)
    shutil.copyfile(package.parents[1] / 'controller/create_rootfs.sh', run / 'control/create_rootfs.sh')
    patch = Path(__file__).with_name('gpu-backtransform-host-staging.patch')
    shutil.copyfile(patch, run / 'control' / patch.name)
    reference = Path('/opt/apps/abacus/abacus-develop-3.9.0.26_nvhpc263gnu/toolchain')
    shutil.copyfile(reference / 'build/elpa-2026.02.001.tar.gz', run / 'input/elpa-2026.02.001.tar.gz')
    shutil.copyfile(reference / 'scripts/stage3/install_elpa.sh', run / 'input/reference/install_elpa.sh')
    shutil.copyfile('/opt/modules/modulefiles/devtools/elpa/2026.02.001-2603-gnu',
                    run / 'input/reference/original-module.tcl')
    path = run / 'control/build.sh'
    text = path.read_text()
    anchor = "flags='-O2 -g -fno-omit-frame-pointer -fopenmp -fno-lto'"
    if text.count(anchor) != 1:
        raise ValueError('unexpected parent ELPA build')
    text = text.replace(anchor,
        'patch --batch --fuzz=0 -p1 -d /workspace/source < /control/' + patch.name + '\n' +
        'cp /control/' + patch.name + ' "$ELPA_ROOT/share/sai/"\n' + anchor)
    path.write_text(text)
    hashes = {str(p.relative_to(run)): hashlib.sha256(p.read_bytes()).hexdigest()
              for folder in ('control', 'input') for p in (run / folder).rglob('*') if p.is_file()}
    (run / 'candidate.json').write_text(json.dumps(dict(
        purpose='diagnostic candidate; no public installation or CP2K acceptance claim',
        parent_git=execute(['git', '-C', package.parents[1], 'rev-parse', 'HEAD']).strip(),
        final_prefix='/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-' + isa + '/nvidia',
        change='host-staged ELPA2 back-transformation; GPU compute and other CUDA-aware paths retained',
        inputs=hashes), indent=2) + '\n')
    for p in (run / 'control').glob('*.sh'):
        execute(['bash', '-n', p])
    partition = '4V100' if isa == 'avx512' else '16V100'
    command = ['sbatch', '--hold', '--parsable', '--partition=' + partition, '--time=30',
               '--job-name=elpa-backtransform-' + isa, '--output=' + str(run / 'slurm-%j.out'),
               run / 'control/build.sbatch', run, isa]
    job = execute(command).strip().split(';')[0]
    if not job.isdigit():
        raise ValueError('ambiguous held submission')
    (run / 'job.id').write_text(job + '\n')
    execute(['scontrol', 'release', job])
    print(json.dumps(dict(job=job, run=str(run))))


if __name__ == '__main__':
    main()
