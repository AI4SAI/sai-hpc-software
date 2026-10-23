#!/usr/bin/env python3
"""Fresh held GPU-only 8V100V0 application with verified node exclusion."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path('/org/org-open-sai/proj-sai-hpc-software')
OLD_ID = '39983165813-1-development-8v100v0-avx512-2026-10-23-cfe5e5d87aaf'
NEW_ID = OLD_ID + '-node-retry1'
EXCLUDED_NODE = '8v100v0n02'
CONTROL = ROOT / 'controller/10992f818af71543bb39cb81e0f60c9f7ede9535' / OLD_ID
SOURCE_SHA = 'cfe5e5d87aaf302953f14eb43587bef2411a8fb2'
RECIPE_SHA = 'ae6467ac2934deecf1d610c9d60cb8c5100cf931d2c27aaa66819ba85ca76a79'


def digest(path):
    path = Path(path)
    if not path.is_file() or path.resolve() != path or path.is_symlink():
        raise ValueError('input must be a canonical regular file: ' + str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write('\n')


def wrapper_source(task):
    return '#!/usr/bin/python3 -I\n' + '''import hashlib
import json
from pathlib import Path
import subprocess
import sys
''' + 'TASK = Path(' + repr(str(task)) + ')\n' + '''
expected = ['--parsable', str(TASK / 'job.sbatch')]
received = sys.argv[1:]
if received != expected:
    raise ValueError('restricted sbatch wrapper received unexpected arguments')
executed = ['/usr/bin/sbatch', '--hold', '--exclude=8v100v0n02', *received]
record = dict(received_argv=received, executed_argv=executed,
    wrapper_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    job_script_sha256=hashlib.sha256((TASK / 'job.sbatch').read_bytes()).hexdigest(),
    sbatch_binary_sha256=hashlib.sha256(Path('/usr/bin/sbatch').read_bytes()).hexdigest())
with (TASK / 'results/sbatch-wrapper-call.json').open('x') as stream:
    json.dump(record, stream, sort_keys=True, indent=2)
    stream.write('\\n')
result = subprocess.run(executed, cwd=TASK, capture_output=True, text=True)
job = result.stdout.strip().split(';')[0]
with (TASK / 'results/sbatch-wrapper-result.json').open('x') as stream:
    json.dump(dict(returncode=result.returncode, job_id=job,
        stdout=result.stdout, stderr=result.stderr), stream, sort_keys=True, indent=2)
    stream.write('\\n')
sys.stdout.write(result.stdout)
sys.stderr.write(result.stderr)
if result.returncode == 0 and not job.isdigit():
    raise ValueError('ambiguous held submission; inspect allocation, do not resubmit')
raise SystemExit(result.returncode)
'''


def main():
    old, new = ROOT / 'runs' / OLD_ID, ROOT / 'runs' / NEW_ID
    request = json.loads((old / 'request.json').read_text())
    identity = request['identity']
    if (identity['source_sha'] != SOURCE_SHA or identity['recipe_sha256'] != RECIPE_SHA
            or request['controller'] != str(CONTROL) or request['target'] != '8v100v0-avx512'
            or request['dependencies_run'] != OLD_ID + '-dependencies'
            or request['jobs'] != 6 or request['minutes'] != 240
            or request['overlay_mb'] != 16384 or (old / 'job.id').read_text().strip() != '1914617'):
        raise ValueError('original source/recipe/checkpoint/resources differ')
    accounting = subprocess.check_output(['sacct', '-X', '-nP', '-j', '1914617',
        '-o', 'JobIDRaw,State,ExitCode,NodeList'], text=True).strip()
    if accounting != '1914617|FAILED|255:0|8v100v0n02':
        raise ValueError('original failed allocation differs: ' + accounting)
    log = (old / 'results/slurm-1914617.log').read_text()
    if ('CP2K_EXACT_DEPENDENCY_CHECKPOINT_CLONED' not in log
            or 'fuse2fs failed to mount' not in log or 'in 10s' not in log
            or 'CP2K_IMAGE_DRIVER sha256=46c1a8c8f42ab2419ea88072e41d89903c453d93f52485b5baddea5ca238bb10' not in log
            or 'Initialized empty Git repository' in log or 'CP2K_APPLICATION_COMPILER' in log
            or (old / 'artifact.path').exists()):
        raise ValueError('not the reviewed pre-build mount timeout')
    preparation = json.loads((old / 'results/overlay-workdir-preparation.json').read_text())
    if (preparation['status'] != 'prepared' or not preparation['source_unchanged']
            or not preparation['upper_directory_unchanged']):
        raise ValueError('original clone did not finish its integrity checks')
    availability = subprocess.check_output(['sinfo', '-N', '-h', '-p', '8V100V0',
        '-o', '%N|%T|%C|%G'], text=True)
    candidates = []
    for line in availability.splitlines():
        node, state, cpus, _ = line.split('|')
        if node != EXCLUDED_NODE and state in ('mixed', 'idle') and int(cpus.split('/')[1]) >= 6:
            candidates.append(node)
    if not candidates:
        raise ValueError('no other healthy 8V100V0 node currently reports at least six idle CPUs')
    updates = old / 'input/cp2k-updates'
    archives = {path.name: digest(path) for path in updates.iterdir()}
    tracked = [old / name for name in ('request.json', 'job.id', 'job.sbatch',
        'results/slurm-1914617.log', 'results/overlay-workdir-preparation.json', 'results/status.json',
        'runtime/apptainer-fuse2fs.conf')]
    deps = ROOT / 'runs' / request['dependencies_run']
    tracked += [deps / name for name in ('request.json', 'job.id', 'job.sbatch', 'results/dependency-stage.json')]
    tracked += list(updates.iterdir())
    control_hashes = {str(path.relative_to(CONTROL)): digest(path)
        for path in CONTROL.rglob('*') if path.is_file() and '__pycache__' not in path.parts}
    tracked += [CONTROL / name for name in control_hashes]
    observed = {str(path): digest(path) for path in tracked}
    env = {key: value for key, value in os.environ.items() if not key.startswith(('PYTHON', 'SBATCH_'))}
    env.update(SAI_SOFTWARE_ROOT=str(ROOT), PYTHONDONTWRITEBYTECODE='1')
    cli = ['/usr/bin/python3', '-B', '-s', str(CONTROL / 'software_controller.py')]
    computed = subprocess.check_output([*cli, 'identity', 'cp2k', SOURCE_SHA,
        identity['source_version'], '8v100v0-avx512', '--track', 'development', '--source-ref', 'master'],
        env=env, text=True)
    if json.loads(computed) != identity:
        raise ValueError('frozen controller no longer computes the original identity')
    new.mkdir(mode=0o700)
    subprocess.run([*cli, 'init', 'cp2k', NEW_ID], env=env, check=True)
    destination = new / 'input/cp2k-updates'
    destination.mkdir()
    for name, expected in archives.items():
        with (updates / name).open('rb') as incoming, (destination / name).open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        if digest(destination / name) != expected:
            raise ValueError('copied archive mismatch: ' + name)
    wrapper_dir = new / 'input/submission-bin'
    wrapper_dir.mkdir(mode=0o700)
    wrapper = wrapper_dir / 'sbatch'
    with wrapper.open('x') as stream:
        stream.write(wrapper_source(new))
    wrapper.chmod(0o700)
    env['PATH'] = str(wrapper_dir) + os.pathsep + env.get('PATH', os.defpath)
    argv = [*cli, 'submit', 'cp2k', NEW_ID, SOURCE_SHA, identity['source_version'], '8v100v0-avx512',
        '--track', 'development', '--source-ref', 'master', '--dependencies-run', request['dependencies_run'],
        '--overlay-mb', str(request['overlay_mb']), '--jobs', str(request['jobs']),
        '--minutes', str(request['minutes'])]
    wrapper_metadata = dict(path=str(wrapper), sha256=digest(wrapper),
        injected_args=['--hold', '--exclude=' + EXCLUDED_NODE])
    plan = dict(purpose=__doc__, prepared_at=datetime.now(timezone.utc).isoformat(),
        original_run=OLD_ID, original_job='1914617', original_accounting=accounting,
        new_run=NEW_ID, identity=identity, excluded_node=EXCLUDED_NODE,
        submission_wrapper=wrapper_metadata,
        cpu_allocation_policy=dict(explicit_cpu_option=False, build_jobs=request['jobs'],
            expected_running_cpus=6, held_cpu_fields='recorded without a guard because the site derives CPU resources from GPU count at scheduling'),
        node_availability=availability, healthy_other_nodes_with_idle_cpus=candidates,
        node_retry_reason='pre-entry fuse2fs 10-second mount timeout on 8v100v0n02; fresh original-checkpoint clone excludes that node; root cause remains unproven',
        controller=str(CONTROL), dependencies_run=request['dependencies_run'],
        controller_file_sha256=control_hashes, recovery_script_sha256=digest(Path(__file__).resolve()),
        input_archives=archives, original_small_file_sha256=observed, argv=argv,
        dependency_hash_verification='original frozen submit and original compute-node clone',
        original_failed_overlay_reused=False, mount_timeout_changed=False,
        original_workflow_success_claimed=False)
    write_json(new / 'input/retry-plan.json', plan)
    if any(digest(Path(name)) != expected for name, expected in observed.items()):
        raise ValueError('original inputs changed before retry')
    write_json(new / 'input/submission-attempt.json', dict(started_at=datetime.now(timezone.utc).isoformat(),
        argv=argv, submission_wrapper=wrapper_metadata,
        rule='one submission only; inspect retained run on any interruption'))
    with (new / 'results/submit.stdout').open('x') as out, (new / 'results/submit.stderr').open('x') as err:
        result = subprocess.run(argv, cwd=ROOT, env=env, stdout=out, stderr=err)
    receipt = dict(returncode=result.returncode, run_id=NEW_ID,
        original_small_files_unchanged=all(digest(Path(name)) == value for name, value in observed.items()))
    if result.returncode == 0:
        receipt['job_id'] = (new / 'job.id').read_text().strip()
        fresh = json.loads((new / 'request.json').read_text())
        if fresh['identity'] != identity or fresh['dependencies_run'] != request['dependencies_run']:
            raise ValueError('submitted retry identity/checkpoint differs; leave held for review')
        receipt['request_sha256'] = digest(new / 'request.json')
        receipt['job_script_sha256'] = digest(new / 'job.sbatch')
        wrapper_call = json.loads((new / 'results/sbatch-wrapper-call.json').read_text())
        wrapper_result = json.loads((new / 'results/sbatch-wrapper-result.json').read_text())
        if (wrapper_result['returncode'] != 0 or wrapper_result['job_id'] != receipt['job_id']
                or wrapper_call['received_argv'] != ['--parsable', str(new / 'job.sbatch')]
                or wrapper_call['executed_argv'] != ['/usr/bin/sbatch', '--hold',
                    '--exclude=' + EXCLUDED_NODE, '--parsable', str(new / 'job.sbatch')]
                or wrapper_call['wrapper_sha256'] != digest(wrapper)
                or wrapper_call['job_script_sha256'] != digest(new / 'job.sbatch')):
            raise ValueError('held wrapper record differs; leave held for review, do not resubmit')
        allocation = subprocess.check_output(['scontrol', 'show', 'job', '-o', receipt['job_id']], text=True)
        receipt['submission_allocation'] = allocation
        fields = dict(item.split('=', 1) for item in allocation.split() if '=' in item)
        expected_fields = dict(JobId=receipt['job_id'], JobState='PENDING', Reason='JobHeldUser',
            Account='proj-sai-hpc-software', Partition='8V100V0', ExcNodeList=EXCLUDED_NODE,
            WorkDir=str(new), Command=str(new / 'job.sbatch'), TimeLimit='04:00:00',
            QOS='flood-1o2gpu', NumTasks='1', TresPerNode='gres/gpu:1')
        receipt['held_allocation_checks'] = {key: fields.get(key) == value for key, value in expected_fields.items()}
        receipt['excluded_node_recorded'] = fields.get('ExcNodeList') == EXCLUDED_NODE
        (new / 'results/submission-allocation.txt').write_text(allocation)
        if not all(receipt['held_allocation_checks'].values()) or not receipt['original_small_files_unchanged']:
            receipt['held_for_review'] = True
            write_json(new / 'results/retry-submitted.json', receipt)
            raise ValueError('held allocation or immutable input checks failed; new job stays held for review; do not resubmit')
        release = subprocess.run(['scontrol', 'release', receipt['job_id']], text=True, capture_output=True)
        receipt['release'] = dict(argv=['scontrol', 'release', receipt['job_id']],
            returncode=release.returncode, stdout=release.stdout, stderr=release.stderr)
        if release.returncode:
            write_json(new / 'results/retry-submitted.json', receipt)
            raise ValueError('release failed; inspect existing held job, do not resubmit')
    write_json(new / 'results/retry-submitted.json', receipt)
    print(json.dumps(receipt, sort_keys=True), flush=True)
    if result.returncode or not receipt['original_small_files_unchanged']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
