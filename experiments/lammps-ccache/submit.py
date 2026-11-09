#!/usr/bin/env python3
"""Build identical pinned LAMMPS sources twice with fresh build overlays."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

SHA = 'f1a6e5cf5167deeb1f70bc8bd38ff8c2e1a853a7'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    control, experiment, source_repo = (Path(value).resolve() for value in sys.argv[1:])
    project = Path('/org/org-open-sai/proj-sai-hpc-software')
    root = project / 'experimental/deepmd-lammps'
    if any(not path.is_relative_to(root) for path in (control, experiment, source_repo)) or experiment.exists():
        raise ValueError('use a fresh project-backed experiment')
    os.environ['SAI_SOFTWARE_ROOT'] = str(project)
    sys.path.insert(0, str(control))
    import md_lammps_standalone as standalone
    from md_ccache import prepare_store
    from remote_controller import private_scratch, project_batch_directives
    production_cache = prepare_store(root, '16v100-avx2')
    dep, files = standalone.dependency('16v100-avx2')
    verified = standalone.verify_dependency(dep, files)
    source = dict(track='development', source_ref='refs/pull/5308/head',
                  source_sha=SHA, source_version='pr5308-2026-11-07')
    delivery = standalone.make_delivery(source, '16v100-avx2')
    experiment.mkdir(parents=True)
    # Experiments own an empty cache; accepted production caches are preserved.
    cache = experiment / 'compiler-cache'
    cache.mkdir(mode=0o700)
    variants = []
    for label in ('cold', 'warm'):
        run = root / 'runs' / (experiment.name + '-' + label)
        if run.exists():
            raise ValueError('run exists; inspect its receipt')
        (run / 'results').mkdir(parents=True)
        for name in ('runtime', 'apptainer-cache'):
            private_scratch(run / name)
        script = standalone.render(delivery, run.name)
        entry = '/control/md_lammps_standalone_entry.sh'
        bind = str(root / 'cache/repositories/lammps') + ':/input/lammps:ro'
        if script.count(entry) != 3 or script.count(bind) != 2:
            raise ValueError('standalone entry/source binding changed')
        script = script.replace(entry, '/control/benchmark/entry.sh')
        script = script.replace(bind, str(source_repo) + ':/input/lammps:ro')
        cache_bind = str(production_cache) + ':/ccache-store:rw'
        if script.count(cache_bind) != 1:
            raise ValueError('standalone cache binding changed')
        script = script.replace(cache_bind, str(cache) + ':/ccache-store:rw')
        script = script.replace('set -eo pipefail',
            "set -eo pipefail\nexport APPTAINERENV_SAI_BENCH_GENERATOR='Unix Makefiles'\n"
            'export APPTAINERENV_SAI_BENCH_VARIANT=pr5308', 1)
        (run / 'job.sbatch').write_text(script)
        subprocess.run(['bash', '-n', str(run / 'job.sbatch')], check=True)
        (run / 'request.json').write_text(json.dumps(dict(delivery=delivery, controller=str(control),
            job_script_sha256=digest(run / 'job.sbatch'), dependency_verification=verified,
            benchmark=dict(cache=label, experiment=str(experiment))), indent=2) + '\n')
        variants.append(dict(label=label, run=str(run), delivery=delivery))
    lines = ['#!/usr/bin/env bash', '#SBATCH --job-name=lmp-ccache-pair',
             '#SBATCH --partition=16V100', '#SBATCH --qos=rush-gpu',
             *project_batch_directives(project, experiment), '#SBATCH --nodes=1',
             '#SBATCH --ntasks=1', '#SBATCH --gpus-per-node=4', '#SBATCH --time=180',
             '#SBATCH --export=NIL', '#SBATCH --output=' + str(experiment / 'slurm-%j.log'),
             'set -euo pipefail', 'export PATH=/usr/bin:/bin',
             'lscpu > ' + shlex.quote(str(experiment / 'lscpu.txt')),
             'scontrol show job -o "$SLURM_JOB_ID" > ' + shlex.quote(str(experiment / 'allocation.txt'))]
    for row in variants:
        lines += ['echo START_' + row['label'].upper(), 'date --iso-8601=seconds',
                  'bash ' + shlex.quote(row['run'] + '/job.sbatch') + ' > ' +
                  shlex.quote(row['run'] + '/results') + '/slurm-"$SLURM_JOB_ID".log 2>&1',
                  'echo FINISHED_' + row['label'].upper(), 'date --iso-8601=seconds']
    script = experiment / 'job.sbatch'
    script.write_text('\n'.join(lines) + '\n')
    pins = {str(p.relative_to(control)): digest(p) for p in control.rglob('*') if p.is_file()}
    record = dict(schema=1, variants=variants, controller=str(control), controller_files=pins,
                  job_script_sha256=digest(script), source_sha=SHA, build_jobs=24,
                  cache=str(cache), initial_cached_manifests_and_results=0,
                  order='cold then warm; separate fresh overlays; OS caches not flushed',
                  scope='unpublished compiler-cache experiment; science requires separate acceptance')
    (experiment / 'request.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
    subprocess.run(['bash', '-n', str(script)], check=True)
    subprocess.run(['sbatch', '--test-only', str(script)], cwd=experiment, check=True)
    command = ['sbatch', '--hold', '--parsable', str(script)]
    (experiment / 'submission-intent.json').write_text(json.dumps(dict(command=command)) + '\n')
    job = subprocess.check_output(command, cwd=experiment, text=True).strip().split(';')[0]
    if not job.isdigit():
        raise ValueError('ambiguous job handle; inspect queue instead of retrying')
    for path in [experiment, *(Path(v['run']) for v in variants)]:
        (path / 'job.id').write_text(job + '\n')
    subprocess.run(['scontrol', 'release', job], check=True)
    print(json.dumps(dict(job=job, experiment=str(experiment), variants=variants)), flush=True)


if __name__ == '__main__':
    main()
