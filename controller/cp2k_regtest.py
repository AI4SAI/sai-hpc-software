#!/usr/bin/env python3
"""Run upstream cuSOLVERMp regressions inside the full daily candidate SIF."""
import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess
import time

import cp2k_acceptance as science
import cp2k_regtest_suite as suite
from cp2k_benchmark import write_manifest
from delivery_layout import validate_record
from remote_controller import project_batch_directives, private_scratch, safe_sha
from software_controller import recipe_fingerprint
from source_cache import checksum

ROOT = science.ROOT
CONTROL = Path(__file__).resolve().parent
PROOF = 'cp2k_regtest'
PURPOSE = 'cp2k-candidate-cusolvermp-regression'
RANK_WRAPPER = '''#!/usr/bin/python3
import sys
sys.path.insert(0, '/control')
from cp2k_regtest_suite import rank
rank()
'''


def eligible(track, target):
    return track == 'development' and target == '16v100-avx2'


def source_reader(source_sha):
    revision = safe_sha(source_sha)
    repository = ROOT / 'cache/repositories/cp2k'
    return lambda path: subprocess.check_output(
        ['git', '--git-dir=' + str(repository), 'show', revision + ':' + path])


def render_job(args):
    identity = validate_record(vars(args))
    if not eligible(identity['track'], args.target) or not 5 <= args.minutes <= 120:
        raise ValueError('cuSOLVERMp tester requires a bounded development/16V100 job')
    task, q = science.run_dir(args.run_id), shlex.quote
    prefix = identity['install_prefix']
    lines = ['#!/usr/bin/env bash', '#SBATCH --job-name=cp2k-cusolvermp-' + task.name,
        '#SBATCH --partition=16V100', '#SBATCH --qos=rush-gpu', '#SBATCH --nodes=1',
        '#SBATCH --ntasks=16', '#SBATCH --gpus-per-node=4', '#SBATCH --time=' + str(args.minutes),
        '#SBATCH --export=NIL', '#SBATCH --output=' + str(task / 'results/slurm-%j.log'),
        *project_batch_directives(ROOT, task), 'set -eo pipefail', 'umask 077',
        'export HOME=' + q(str(Path.home())),
        'export USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?}',
        'export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
        'export LD_LIBRARY_PATH="" LD_PRELOAD=""',
        'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
        'export TMPDIR=' + q(str(task / 'tmp')),
        'export APPTAINER_TMPDIR=' + q(str(task / 'apptainer-runtime')),
        'export APPTAINER_CACHEDIR=' + q(str(task / 'apptainer-cache')),
        'source /etc/profile.d/lmod.sh', 'module purge',
        'module use /opt/modules/modulefiles/devtools /opt/modules/modulefiles/apps',
        'module load apptainer/1.4.4 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto', 'set -u',
        'cd ' + q(str(task)),
        'scontrol show job -o "$SLURM_JOB_ID" > results/allocation.txt',
        'scontrol show hostnames "$SLURM_JOB_NODELIST" > results/nodes.txt',
        'module -t list > results/modules.txt 2>&1',
        'command -v mpirun > results/mpirun.txt', 'mpirun --version > results/mpi-version.txt',
        'args=(apptainer exec --nv --cleanenv --no-home --no-mount bind-paths,home,cwd,tmp,hostfs)',
        'for path in /usr /lib /lib64 /opt/devtools; do args+=(--bind "$path:$path:ro"); done',
        'args+=(--bind ' + q(str(CONTROL) + ':/control:ro') +
        ' --bind ' + q(f'{task}:{task}:rw') + ' --pwd ' + q(str(task)) + ')',
        'args+=(--env "TMPDIR=$TMPDIR" --env "CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:?}"',
        '       --env PYTHONDONTWRITEBYTECODE=1)',
        # Use this image's recorded runtime, including its private dependencies.
        # MPI and the upstream harness stay inside the same container namespace.
        '"${args[@]}" ' + q(args.artifact) + ' /usr/bin/bash --noprofile --norc -c ' +
        q('driver=${LD_LIBRARY_PATH:-}; source "$1"; '
          'export LD_LIBRARY_PATH="${LD_LIBRARY_PATH}${driver:+:$driver}"; '
          'exec /usr/bin/python3 /control/cp2k_regtest_suite.py "$2" "$3"') +
        ' bash ' + q(prefix + '/share/sai/runtime-env.sh') + ' ' + q(str(task)) + ' "$(command -v mpirun)"',
        'echo CP2K_CUSOLVERMP_REGTEST_FINISHED']
    return '\n'.join(lines) + '\n'


def verify_evidence(task, request):
    files = {}
    read = lambda path: suite.read_file(task, path, files)
    if task != science.run_dir(request['run_id']) or json.loads(read('request.json')) != request:
        raise ValueError('regression request differs from its task')
    identity = science.request_identity(request)
    science.request_artifact(request)
    if (request.get('purpose') != PURPOSE or
            not eligible(identity['track'], identity['target']) or
            recipe_fingerprint('cp2k', CONTROL) != identity['recipe_sha256']):
        raise ValueError('regression candidate recipe or scope changed')
    launcher = CONTROL / 'cp2k_regtest_suite.py'
    if str(launcher) != request.get('launcher') or not CONTROL.is_relative_to(ROOT / 'controller'):
        raise ValueError('regression launcher differs from trusted controller')
    for path, key in ((CONTROL / 'cp2k_regtest.py', 'controller_sha256'),
                      (launcher, 'launcher_sha256'),
                      (CONTROL / 'cp2k_feature_contract.py', 'helpers_sha256')):
        if path.resolve() != path or path.is_symlink() or checksum(path) != request.get(key):
            raise ValueError('regression controller changed: ' + key)
    script = read('job.sbatch')
    if (suite.digest(script.encode()) != request.get('job_script_sha256') or
            script != render_job(argparse.Namespace(**request)) or
            read('bin/cp2k.psmp') != RANK_WRAPPER or
            request.get('rank_wrapper_sha256') != suite.digest(RANK_WRAPPER.encode())):
        raise ValueError('regression job or rank wrapper changed')
    if request['suite_files'] != suite.prepare(source_reader(identity['source_sha'])):
        raise ValueError('regression suite differs from the candidate source tree')
    job = read('job.id').strip()
    if not job.isdigit() or job != request.get('job'):
        raise ValueError('regression Slurm handle changed')
    if any(request.get(key) != value for key, value in suite.RESOURCES.items()):
        raise ValueError('regression requested resources changed')
    nodes = read('results/nodes.txt').splitlines()
    slurm = dict(re.findall(r'(?:^|\s)([A-Za-z][A-Za-z0-9/]*)=([^\s]+)', read('results/allocation.txt')))
    if (len(nodes) != 1 or slurm.get('JobId') != job or slurm.get('Partition') != '16V100' or
            slurm.get('NumNodes') != '1' or slurm.get('NumTasks') != '16' or
            slurm.get('TresPerNode') != 'gres/gpu:4' or int(slurm.get('NumCPUs', '0')) < 16):
        raise ValueError('regression allocation differs from four-GPU request')
    mpi = read('results/mpirun.txt').strip()
    if (not mpi.startswith('/opt/devtools/openmpi/') or not mpi.endswith('-avx2/bin/mpirun') or
            'Open MPI' not in read('results/mpi-version.txt') or
            'openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto' not in read('results/modules.txt')):
        raise ValueError('unexpected regression MPI runtime')
    if 'CP2K_CUSOLVERMP_REGTEST_FINISHED' not in read(f'results/slurm-{job}.log'):
        raise ValueError('regression container did not finish')
    results = suite.verify_results(task, request)
    read('results/report.txt')
    if re.findall(r'^Status: (\S+)$', (task / 'results/report.txt').read_text(), re.M)[-1:] != ['OK']:
        raise ValueError('regression final report is not OK')
    results['files'].update(files)
    return dict(schema=1, purpose=PURPOSE, run_id=request['run_id'], job=job, identity=identity,
                artifact_sha256=request['artifact_sha256'], resources=suite.RESOURCES, **results)


def submit(args):
    artifact, manifest = science.build_artifact(args.build_run_id, args.version, args.target)
    identity = manifest['identity']
    if (not eligible(identity['track'], args.target) or
            recipe_fingerprint('cp2k', CONTROL) != identity['recipe_sha256']):
        raise ValueError('regression requires this recipe\'s development candidate')
    task = science.run_dir(args.run_id)
    task.parent.mkdir(exist_ok=True)
    task.mkdir(mode=0o700)  # Never resubmit an ambiguous previous allocation.
    for name in ('results', 'bin', 'tmp', 'apptainer-runtime', 'apptainer-cache'):
        private_scratch(task / name)
    request = dict(vars(args), **suite.RESOURCES, schema=1, purpose=PURPOSE, software='cp2k',
        identity=identity, install_prefix=identity['install_prefix'], source_sha=identity['source_sha'],
        recipe_sha256=identity['recipe_sha256'], artifact=str(artifact), artifact_sha256=manifest['sha256'],
        launcher=str(CONTROL / 'cp2k_regtest_suite.py'),
        launcher_sha256=checksum(CONTROL / 'cp2k_regtest_suite.py'),
        controller_sha256=checksum(CONTROL / 'cp2k_regtest.py'),
        helpers_sha256=checksum(CONTROL / 'cp2k_feature_contract.py'),
        suite_files=suite.prepare(source_reader(identity['source_sha']), task))
    script, wrapper = task / 'job.sbatch', task / 'bin/cp2k.psmp'
    script.write_text(render_job(argparse.Namespace(**request)))
    wrapper.write_text(RANK_WRAPPER)
    script.chmod(0o700)
    wrapper.chmod(0o700)
    subprocess.run(['bash', '-n', str(script)], check=True)
    request['job_script_sha256'] = checksum(script)
    request['rank_wrapper_sha256'] = checksum(wrapper)
    (task / 'request.json').write_text(json.dumps(request, sort_keys=True) + '\n')
    response = subprocess.run(['sbatch', '--hold', '--parsable', str(script)], check=True,
                              text=True, capture_output=True, cwd=task)
    job = response.stdout.strip().split(';')[0]
    if not job.isdigit():
        raise ValueError('invalid regression sbatch response; inspect held allocation before retry')
    (task / 'job.id').write_text(job + '\n')
    request['job'] = job
    (task / 'request.json').write_text(json.dumps(request, sort_keys=True) + '\n')
    subprocess.run(['scontrol', 'release', job], check=True)
    print(job, flush=True)


def monitor(args):
    task = science.run_dir(args.run_id)
    job = suite.read_file(task, 'job.id').strip()
    if not job.isdigit():
        raise ValueError('invalid regression Slurm handle')
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        queue = subprocess.run(['squeue', '-h', '-j', job, '-o', '%T|%R'], check=True, text=True, capture_output=True)
        if queue.stdout.strip():
            print(job + ': ' + queue.stdout.strip(), flush=True)
        else:
            rows = subprocess.check_output(['sacct', '-X', '-nP', '-j', job, '-o', 'JobIDRaw,State,ExitCode'], text=True)
            records = [row.split('|') for row in rows.splitlines() if row.split('|')[0] == job]
            if len(records) > 1:
                raise ValueError('ambiguous regression accounting')
            if records and records[0][1] not in ('PENDING', 'RUNNING', 'COMPLETING', 'CONFIGURING'):
                state, exit_code = records[0][1:3]
                request = json.loads(suite.read_file(task, 'request.json'))
                status = dict(job=job, state=state, exit_code=exit_code, verified=False)
                write_manifest(task / 'results/status.json', status)
                try:
                    artifact, manifest = science.request_artifact(request)
                    if manifest.get(PROOF, {}).get('run_id') == args.run_id:
                        manifest.pop(PROOF)
                        write_manifest(artifact.with_suffix('.json'), manifest)
                    if (state, exit_code) != ('COMPLETED', '0:0'):
                        raise ValueError(f'Slurm {state}, exit {exit_code}; see regression logs')
                    evidence = verify_evidence(task, request)
                except (ValueError, OSError, KeyError) as exc:
                    suite.write_report(task, request, str(exc))
                    print(str(exc), flush=True)
                    return 1
                path = task / 'results/evidence.json'
                write_manifest(path, evidence)
                manifest[PROOF] = dict(run_id=args.run_id, job=job, verified=True, identity=request['identity'],
                    evidence_sha256=checksum(path), **{name: request[name] for name in (
                        'artifact_sha256', 'controller_sha256', 'launcher', 'launcher_sha256')})
                write_manifest(artifact.with_suffix('.json'), manifest)
                status['verified'] = True
                write_manifest(task / 'results/status.json', status)
                print('|'.join(records[0]), flush=True)
                return 0
        time.sleep(args.interval)
    raise TimeoutError('regression deadline reached; job has not been cancelled: ' + job)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='op', required=True)
    command = commands.add_parser('submit')
    command.add_argument('run_id')
    command.add_argument('version')
    command.add_argument('target', choices=('16v100-avx2',))
    command.add_argument('--build-run-id', required=True)
    command.add_argument('--minutes', type=int, default=20)
    command = commands.add_parser('monitor')
    command.add_argument('run_id')
    command.add_argument('--timeout', type=int, default=14400)
    command.add_argument('--interval', type=int, default=15)
    args = parser.parse_args()
    return {'submit': submit, 'monitor': monitor}[args.op](args) or 0


if __name__ == '__main__':
    raise SystemExit(main())
