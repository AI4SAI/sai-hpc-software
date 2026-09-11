#!/usr/bin/env python3
"""Isolated paired-stack candidate builds; never changes production modules.

Successful compilation creates an unpublished candidate. This experimental
controller deliberately has no implicit publish operation: scientific and
performance acceptance is a separate, required lifecycle stage.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
from md_tracking import fingerprint, TARGETS as MD_TARGETS
from remote_controller import TARGETS, container_command, safe_name, safe_sha
from source_cache import checksum

PROJECT = Path(os.environ.get('SAI_SOFTWARE_ROOT', Path.home() / 'sai-hpc-software')).resolve()
ROOT = PROJECT / 'experimental/deepmd-lammps'
CONTROL = Path(__file__).resolve().parent


def join(argv):
    return shlex.join(list(map(str, argv)))


def run(argv, **kwargs):
    return subprocess.run(list(map(str, argv)), check=True, text=True, **kwargs)


def task(run_id):
    path = ROOT / 'runs' / safe_name(run_id)
    if path.resolve() != path:
        raise ValueError('symlinked task root')
    return path


def validate_pair(request):
    if request.get('schema') != 1 or request.get('target') not in MD_TARGETS:
        raise ValueError('unsupported MD source contract')
    safe_name(request['version'])
    if set(request['sources']) != {'deepmd-kit', 'lammps'}:
        raise ValueError('both independently resolved upstream revisions are required')
    for source in request['sources'].values():
        safe_sha(source['sha'])
    return request


def render(request, run_id, jobs=6, minutes=180, overlay_mb=32768):
    validate_pair(request)
    target = TARGETS[request['target']]
    if not 1 <= jobs <= min(6, target.get('build_jobs', 6)) or not 1 <= minutes <= 180:
        raise ValueError('MD build resources outside experimental bounds')
    if not 8192 <= overlay_mb <= 65536:
        raise ValueError('overlay capacity outside policy')
    r = task(run_id)
    image = PROJECT / 'containers/base/minimal-v1.sif'
    overlay = r / 'work.ext3'
    result = r / 'candidate.sif'
    artifact = ROOT / 'containers/software/deepmd-lammps' / request['version'] / request['target'] / (run_id + '.sif')
    args = ['/usr/bin/bash', '/control/md_container_entry.sh', 'build', request['version'], request['target'],
            request['sources']['deepmd-kit']['sha'], request['sources']['lammps']['sha']]
    def container(phase, final=False):
        command = args.copy()
        command[2] = phase
        sources = [(ROOT / 'cache/repositories' / name, '/input/' + name) for name in request['sources']]
        return container_command(result if final else image, command, overlay=None if final else overlay,
                                 control=CONTROL, jobs=jobs, gpu=True,
                                 extra_binds=[('/opt/apps', '/opt/apps'), *(sources if not final else [])])
    emit = container_command(image, ['/usr/bin/cat', '/workspace/final.squashfs'],
                             overlay=str(overlay) + ':ro', jobs=jobs)
    lines = ['#!/usr/bin/env bash', f'#SBATCH --job-name=md-{run_id}',
             f'#SBATCH --partition={target["partition"]}', f'#SBATCH --qos={target["qos"]}',
             '#SBATCH --nodes=1', '#SBATCH --ntasks=1', '#SBATCH --gpus-per-node=1',
             f'#SBATCH --time={minutes}', f'#SBATCH --output={r}/results/slurm-%j.log',
             '#SBATCH --export=NIL', 'set -eo pipefail', 'export PATH=/usr/bin:/bin',
             'export LD_LIBRARY_PATH="" LD_PRELOAD=""', 'source /etc/profile.d/lmod.sh',
             'module load apptainer/1.4.4', 'set -u', 'umask 077',
             f'export TMPDIR={shlex.quote(str(r / "runtime"))}',
             f'export APPTAINER_TMPDIR={shlex.quote(str(r / "runtime"))}',
             f'export APPTAINER_CACHEDIR={shlex.quote(str(r / "apptainer-cache"))}',
             'export APPTAINERENV_SAI_MD_ALLOCATED_JOB="$SLURM_JOB_ID"',
             'export APPTAINERENV_SAI_MD_ALLOCATED_NODE="$(hostname)"',
             'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
             join(['test', '-s', image]), join(['test', '!', '-e', overlay]),
             join(['apptainer', 'overlay', 'create', '--fakeroot', '--sparse', '--size', str(overlay_mb), overlay]),
             join(container('build')), join(container('export')),
             join(emit) + ' > ' + shlex.quote(str(r / 'final.squashfs')),
             join(['apptainer', 'sif', 'new', result]),
             join(['apptainer', 'sif', 'add', '--datatype', '4', '--partfs', '1', '--parttype', '2',
                         '--partarch', '2', '--groupid', '1', result, r / 'final.squashfs']),
             join(container('verify', final=True)),
             join(['mkdir', '-p', artifact.parent]), join(['test', '!', '-e', artifact]),
             join(['chmod', '0444', result]), join(['mv', result, artifact]),
             f"printf '%s\\n' {shlex.quote(str(artifact))} > {shlex.quote(str(r / 'artifact.path'))}",
             'echo MD_CANDIDATE_BUILT_NOT_PUBLISHED']
    # Keep failed overlays for diagnosis. No source/install tree is ever on host.
    return '\n'.join(lines) + '\n'


def submit_probe(args):
    """One GPU, five minutes, no source checkout or production mutations."""
    if args.target not in MD_TARGETS:
        raise ValueError('unvalidated MD probe target')
    r = task(args.run_id)
    if (r / 'job.id').exists():
        raise ValueError('probe already submitted')
    for part in ('results', 'runtime', 'apptainer-cache'):
        (r / part).mkdir(parents=True, exist_ok=True)
    target = TARGETS[args.target]
    baseline = args.op == 'baseline'
    probe_script = {'baseline': 'md_baseline.sh', 'probe': 'md_native_probe.sh',
                    'import-probe': 'md_import_probe.sh', 'conversion-probe': 'md_import_probe.sh'}[args.op]
    entry = ['/usr/bin/bash', '/control/' + probe_script, args.target]
    binds = [('/opt/apps', '/opt/apps')]
    if baseline or args.op == 'conversion-probe':
        entry.append(safe_sha(args.sha))
        repository = ROOT / 'cache/repositories/deepmd-kit'
        run(['git', '--git-dir', repository, 'cat-file', '-e', args.sha + '^{commit}'])
        binds.append((repository, '/input/deepmd-kit'))
    command = container_command(PROJECT / 'containers/base/minimal-v1.sif', entry, overlay=r / 'probe.ext3',
        control=CONTROL, jobs=1, gpu=True, extra_binds=binds)
    lines = ['#!/usr/bin/env bash', f'#SBATCH --job-name={args.run_id}',
             f'#SBATCH --partition={target["partition"]}', f'#SBATCH --qos={target["qos"]}',
             '#SBATCH --nodes=1', '#SBATCH --ntasks=1', '#SBATCH --gpus-per-node=1', '#SBATCH --time=' + ('10' if baseline else '5'),
             f'#SBATCH --output={r}/results/slurm-%j.log', '#SBATCH --export=NIL',
             'set -eo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""',
             'source /etc/profile.d/lmod.sh', 'module load apptainer/1.4.4', 'set -u',
             f'export TMPDIR={shlex.quote(str(r / "runtime"))} APPTAINER_TMPDIR={shlex.quote(str(r / "runtime"))}',
             f'export APPTAINER_CACHEDIR={shlex.quote(str(r / "apptainer-cache"))}',
             'export APPTAINERENV_SAI_MD_ALLOCATED_JOB="$SLURM_JOB_ID"',
             'export APPTAINERENV_SAI_MD_ALLOCATED_NODE="$(hostname)"',
             'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
             join(['test', '!', '-e', r / 'probe.ext3']),
             join(['apptainer', 'overlay', 'create', '--fakeroot', '--sparse', '--size', '8192' if baseline else '1024', r / 'probe.ext3']),
             join(command)]
    script = r / 'job.sbatch'
    script.write_text('\n'.join(lines) + '\n')
    run(['bash', '-n', script])
    job = run(['sbatch', '--parsable', script], capture_output=True).stdout.strip().split(';')[0]
    if not job.isdigit():
        raise ValueError('invalid probe job handle')
    (r / 'job.id').write_text(job + '\n')
    (r / 'request.json').write_text(json.dumps({'target': args.target, 'job': job,
        'job_script_sha256': checksum(script), 'recipe_sha256': fingerprint(CONTROL)}) + '\n')
    print(job, flush=True)


def submit(args):
    request = validate_pair(json.loads(Path(args.request).read_text()))
    r = task(args.run_id)
    if (r / 'job.id').exists():
        raise ValueError('run already submitted; do not restart a live handle')
    for part in ('input', 'results', 'runtime', 'apptainer-cache'):
        (r / part).mkdir(parents=True, exist_ok=True)
    for name, source in request['sources'].items():
        run(['git', '--git-dir', ROOT / 'cache/repositories' / name, 'cat-file', '-e', source['sha'] + '^{commit}'])
    request.update(run_id=args.run_id, recipe_sha256=fingerprint(CONTROL), controller=str(CONTROL))
    script = r / 'job.sbatch'
    script.write_text(render(request, args.run_id, args.jobs, args.minutes, args.overlay_mb))
    run(['bash', '-n', script])
    request['job_script_sha256'] = checksum(script)
    (r / 'request.json').write_text(json.dumps(request, sort_keys=True) + '\n')
    job = run(['sbatch', '--parsable', script], capture_output=True).stdout.strip().split(';')[0]
    if not job.isdigit():
        raise ValueError('invalid Slurm job handle')
    (r / 'job.id').write_text(job + '\n')
    print(job, flush=True)


def monitor(args):
    r = task(args.run_id)
    job = (r / 'job.id').read_text().strip()
    if not job.isdigit():
        raise ValueError('invalid Slurm job handle')
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        active = run(['squeue', '-h', '-j', job, '-o', '%T|%R'], capture_output=True).stdout.strip()
        if active:
            print(job + ': ' + active, flush=True)
        else:
            rows = run(['sacct', '-X', '-n', '-P', '-j', job, '-o', 'JobIDRaw,State,ExitCode'], capture_output=True).stdout.splitlines()
            row = next((x.split('|') for x in rows if x.split('|')[0] == job), None)
            if row and row[1] not in ('RUNNING', 'PENDING', 'COMPLETING'):
                status = {'job': job, 'state': row[1], 'exit_code': row[2], 'build_verified': False,
                          'scientific_verified': False, 'published': False}
                (r / 'results/status.json').write_text(json.dumps(status) + '\n')
                if row[1:3] != ['COMPLETED', '0:0']:
                    return 1
                request = json.loads((r / 'request.json').read_text())
                artifact = Path((r / 'artifact.path').read_text().strip())
                expected = ROOT / 'containers/software/deepmd-lammps' / request['version'] / request['target'] / (args.run_id + '.sif')
                if (artifact != expected or artifact.resolve() != artifact or artifact.is_symlink()
                        or request['recipe_sha256'] != fingerprint(CONTROL)
                        or request['job_script_sha256'] != checksum(r / 'job.sbatch')):
                    raise ValueError('candidate provenance changed')
                status['build_verified'] = True
                status.update(artifact=str(artifact), artifact_sha256=checksum(artifact),
                              recipe_sha256=request['recipe_sha256'], sources=request['sources'])
                (r / 'results/status.json').write_text(json.dumps(status, sort_keys=True) + '\n')
                artifact.with_suffix('.json').write_text(json.dumps(status, sort_keys=True) + '\n')
                print('CANDIDATE_ONLY: scientific/performance acceptance still required', flush=True)
                return 0
        time.sleep(args.interval)
    raise TimeoutError(f'observation deadline; Slurm job {job} was not cancelled or restarted')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='op', required=True)
    submit_parser = sub.add_parser('submit')
    submit_parser.add_argument('run_id'); submit_parser.add_argument('request')
    submit_parser.add_argument('--jobs', type=int, default=6)
    submit_parser.add_argument('--minutes', type=int, default=180)
    submit_parser.add_argument('--overlay-mb', type=int, default=32768)
    monitor_parser = sub.add_parser('monitor')
    monitor_parser.add_argument('run_id'); monitor_parser.add_argument('--timeout', type=int, default=21600)
    monitor_parser.add_argument('--interval', type=int, default=30)
    probe_parser = sub.add_parser('probe')
    probe_parser.add_argument('run_id'); probe_parser.add_argument('target', choices=MD_TARGETS)
    baseline_parser = sub.add_parser('baseline')
    baseline_parser.add_argument('run_id'); baseline_parser.add_argument('target', choices=MD_TARGETS); baseline_parser.add_argument('sha')
    import_parser = sub.add_parser('import-probe')
    import_parser.add_argument('run_id'); import_parser.add_argument('target', choices=MD_TARGETS)
    conversion_parser = sub.add_parser('conversion-probe')
    conversion_parser.add_argument('run_id'); conversion_parser.add_argument('target', choices=MD_TARGETS)
    conversion_parser.add_argument('sha')
    args = parser.parse_args()
    raise SystemExit({'submit': submit, 'monitor': monitor, 'probe': submit_probe,
                      'baseline': submit_probe, 'import-probe': submit_probe,
                      'conversion-probe': submit_probe}[args.op](args))
