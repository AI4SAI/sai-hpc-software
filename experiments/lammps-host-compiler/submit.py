#!/usr/bin/env python3
"""Compare pinned LAMMPS PR 5308 and its base, both clean Make builds."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

BASE = 'd291d572cf3c73a8a720774b78482a7c931304ca'
HEAD = 'f1a6e5cf5167deeb1f70bc8bd38ff8c2e1a853a7'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    control, experiment, source_repo = (Path(value).resolve() for value in sys.argv[1:])
    project = Path('/org/org-open-sai/proj-sai-hpc-software')
    root = project / 'experimental/deepmd-lammps'
    if any(not path.is_relative_to(root) for path in (control, experiment, source_repo)) or experiment.exists():
        raise ValueError('use a fresh project-backed experiment')
    git = ['git', '--git-dir', str(source_repo)]
    if subprocess.check_output([*git, 'rev-parse', HEAD + '^'], text=True).strip() != BASE:
        raise ValueError('upstream PR parent changed')
    if subprocess.check_output([*git, 'diff', '--name-only', BASE, HEAD], text=True).splitlines() != ['cmake/CMakeLists.txt']:
        raise ValueError('unexpected upstream changes')
    if (source_repo / 'objects/info/alternates').exists():
        raise ValueError('source repository must be self-contained inside its container bind')
    os.environ['SAI_SOFTWARE_ROOT'] = str(project)
    sys.path.insert(0, str(control))
    import md_lammps_standalone as standalone
    from remote_controller import private_scratch, project_batch_directives
    dep, files = standalone.dependency('16v100-avx2')
    dependency_check = standalone.verify_dependency(dep, files)
    experiment.mkdir(parents=True)
    diff = subprocess.check_output([*git, 'diff', '--binary', BASE, HEAD])
    (experiment / 'upstream.diff').write_bytes(diff)
    variants = []
    # Candidate first avoids giving it the second run's warmer OS cache.
    for label, sha, ref, version in (
            ('pr5308', HEAD, 'refs/pull/5308/head', 'pr5308-2026-11-07'),
            ('base', BASE, 'develop', 'develop-2026-11-06')):
        source = dict(track='development', source_ref=ref, source_sha=sha, source_version=version)
        delivery = standalone.make_delivery(source, '16v100-avx2')
        run = root / 'runs' / (experiment.name + '-' + label)
        if run.exists():
            raise ValueError('variant exists; inspect its recorded submission')
        (run / 'results').mkdir(parents=True)
        for name in ('runtime', 'apptainer-cache'):
            private_scratch(run / name)
        script = standalone.render(delivery, run.name)
        entry = '/control/md_lammps_standalone_entry.sh'
        bind = str(root / 'cache/repositories/lammps') + ':/input/lammps:ro'
        if script.count(entry) != 3 or script.count(bind) != 2:
            raise ValueError('standalone recipe entry/source binding changed')
        script = script.replace(entry, '/control/benchmark/entry.sh')
        script = script.replace(bind, str(source_repo) + ':/input/lammps:ro')
        script = script.replace('set -eo pipefail',
            'set -eo pipefail\nexport APPTAINERENV_SAI_BENCH_GENERATOR=\'Unix Makefiles\'\n'
            'export APPTAINERENV_SAI_BENCH_VARIANT=' + label, 1)
        (run / 'job.sbatch').write_text(script)
        subprocess.run(['bash', '-n', str(run / 'job.sbatch')], check=True)
        (run / 'request.json').write_text(json.dumps(dict(delivery=delivery, controller=str(control),
            job_script_sha256=digest(run / 'job.sbatch'), dependency_verification=dependency_check,
            benchmark=dict(generator='Unix Makefiles', variant=label, experiment=str(experiment))), indent=2) + '\n')
        variants.append(dict(label=label, run=str(run), delivery=delivery, generator='Unix Makefiles'))
    lines = ['#!/usr/bin/env bash', '#SBATCH --job-name=lmp-pr5308-pair',
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
        lines += ['echo START_' + variant['label'].upper(), 'date --iso-8601=seconds',
                  'bash ' + shlex.quote(str(run / 'job.sbatch')) + ' > ' +
                  shlex.quote(str(run / 'results')) + '/slurm-"$SLURM_JOB_ID".log 2>&1',
                  'echo FINISHED_' + variant['label'].upper(), 'date --iso-8601=seconds']
    script = experiment / 'job.sbatch'
    script.write_text('\n'.join(lines) + '\n')
    pins = {str(path.relative_to(control)): digest(path) for path in control.rglob('*') if path.is_file()}
    record = dict(schema=1, variants=variants, controller=str(control), controller_files=pins,
        job_script_sha256=digest(script), upstream=dict(base=BASE, head=HEAD, diff_sha256=digest(experiment / 'upstream.diff')),
        source_repo=str(source_repo), build_jobs=24, order='pr5308 then base; OS caches not flushed',
        scope='unpublished clean build comparison; no compiler/object cache; not production acceptance')
    (experiment / 'request.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
    subprocess.run(['bash', '-n', str(script)], check=True)
    subprocess.run(['sbatch', '--test-only', str(script)], cwd=experiment, check=True)
    command = ['sbatch', '--hold', '--parsable', str(script)]
    (experiment / 'submission-intent.json').write_text(json.dumps(dict(command=command)) + '\n')
    job = subprocess.check_output(command, cwd=experiment, text=True).strip().split(';')[0]
    if not job.isdigit():
        raise ValueError('ambiguous submission response; inspect queue before retrying')
    for directory in [experiment, *(Path(row['run']) for row in variants)]:
        (directory / 'job.id').write_text(job + '\n')
    subprocess.run(['scontrol', 'release', job], check=True)
    print(json.dumps(dict(job=job, experiment=str(experiment), variants=variants)), flush=True)


if __name__ == '__main__':
    main()
