#!/usr/bin/env python3
"""Run clean Ninja and Make builds sequentially within one 16V100 allocation.

Call on the cluster with a frozen controller snapshot (including benchmark/
entry.sh, measure.py and checksum-verified ninja), and a new experiment path.
No source or installed dependency is rebuilt between the paired runs except
LAMMPS itself. The two runs have distinct, initially empty overlays.
"""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    control, experiment = (Path(value).resolve() for value in sys.argv[1:])
    project = Path('/org/org-open-sai/proj-sai-hpc-software')
    root = project / 'experimental/deepmd-lammps'
    if not control.is_relative_to(root) or not experiment.is_relative_to(root) or experiment.exists():
        raise ValueError('use a new project-backed experiment and frozen control path')
    os.environ['SAI_SOFTWARE_ROOT'] = str(project)
    sys.path.insert(0, str(control))
    import md_lammps_standalone as standalone
    from remote_controller import private_scratch, project_batch_directives
    source = dict(track='development', source_ref='develop',
                  source_sha='d291d572cf3c73a8a720774b78482a7c931304ca',
                  source_version='develop-2026-11-06')
    delivery = standalone.make_delivery(source, '16v100-avx2')
    dep, files = standalone.dependency('16v100-avx2')
    dependency_check = standalone.verify_dependency(dep, files)
    for name, sha in (('lammps', source['source_sha']), ('deepmd-kit', dep['identity']['source_sha'])):
        subprocess.run(['git', '--git-dir', str(root / 'cache/repositories' / name),
                        'cat-file', '-e', sha + '^{commit}'], check=True)
    experiment.mkdir(parents=True)
    variants = []
    for label, generator in (('ninja', 'Ninja'), ('make', 'Unix Makefiles')):
        run = root / 'runs' / (experiment.name + '-' + label)
        if run.exists():
            raise ValueError('variant already exists; inspect its recorded job')
        (run / 'results').mkdir(parents=True)
        for name in ('runtime', 'apptainer-cache'):
            private_scratch(run / name)
        script = standalone.render(delivery, run.name)
        script = script.replace('/control/md_lammps_standalone_entry.sh', '/control/benchmark/entry.sh')
        script = script.replace('set -eo pipefail',
            'set -eo pipefail\nexport APPTAINERENV_SAI_BENCH_GENERATOR=' + shlex.quote(generator), 1)
        (run / 'job.sbatch').write_text(script)
        subprocess.run(['bash', '-n', str(run / 'job.sbatch')], check=True)
        request = dict(delivery=delivery, controller=str(control),
                       job_script_sha256=digest(run / 'job.sbatch'), dependency_verification=dependency_check,
                       benchmark=dict(generator=generator, experiment=str(experiment)))
        (run / 'request.json').write_text(json.dumps(request, indent=2, sort_keys=True) + '\n')
        variants.append(dict(label=label, generator=generator, run=str(run)))
    lines = ['#!/usr/bin/env bash', '#SBATCH --job-name=lmp-ninja-make',
             '#SBATCH --partition=16V100', '#SBATCH --qos=rush-gpu',
             *project_batch_directives(project, experiment), '#SBATCH --nodes=1',
             '#SBATCH --ntasks=1', '#SBATCH --gpus-per-node=4', '#SBATCH --time=240',
             '#SBATCH --export=NIL', '#SBATCH --output=' + str(experiment / 'slurm-%j.log'),
             'set -euo pipefail', 'export PATH=/usr/bin:/bin',
             'lscpu > ' + shlex.quote(str(experiment / 'lscpu.txt')),
             'scontrol show job -o "$SLURM_JOB_ID" > ' + shlex.quote(str(experiment / 'allocation.txt')),
             'nvidia-smi > ' + shlex.quote(str(experiment / 'nvidia-smi.txt'))]
    for variant in variants:
        run = Path(variant['run'])
        lines += ['echo START_' + variant['label'].upper(),
                  'date --iso-8601=seconds',
                  'bash ' + shlex.quote(str(run / 'job.sbatch')) + ' > ' +
                  shlex.quote(str(run / 'results')) + '/slurm-"$SLURM_JOB_ID".log 2>&1',
                  'echo FINISHED_' + variant['label'].upper(), 'date --iso-8601=seconds']
    script = experiment / 'job.sbatch'
    script.write_text('\n'.join(lines) + '\n')
    pins = {str(path.relative_to(control)): digest(path) for path in control.rglob('*') if path.is_file()}
    record = dict(schema=1, variants=variants, controller=str(control), controller_files=pins,
                  job_script_sha256=digest(script), delivery=delivery, build_jobs=24,
                  order='ninja then make; OS page caches not flushed',
                  scope='unpublished generator experiment; not a production acceptance receipt')
    (experiment / 'request.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
    subprocess.run(['bash', '-n', str(script)], check=True)
    subprocess.run(['sbatch', '--test-only', str(script)], cwd=experiment, check=True)
    # Persist both intent and the returned ID before releasing. No automatic
    # retry after an ambiguous submission response.
    (experiment / 'submission-intent.json').write_text(json.dumps(dict(command=['sbatch', '--hold', '--parsable', str(script)])) + '\n')
    job = subprocess.check_output(['sbatch', '--hold', '--parsable', str(script)],
                                  cwd=experiment, text=True).strip().split(';')[0]
    if not job.isdigit():
        raise ValueError('invalid Slurm response; inspect queue before retrying')
    for directory in [experiment, *(Path(row['run']) for row in variants)]:
        (directory / 'job.id').write_text(job + '\n')
    subprocess.run(['scontrol', 'release', job], check=True)
    print(json.dumps(dict(job=job, experiment=str(experiment), variants=variants)), flush=True)


if __name__ == '__main__':
    main()
