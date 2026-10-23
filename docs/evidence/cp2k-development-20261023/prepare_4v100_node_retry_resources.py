#!/usr/bin/env python3
"""Fresh held application with explicit eight-CPU request and verified node exclusion."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path('/org/org-open-sai/proj-sai-hpc-software')
OLD_ID = '39983165813-1-development-4v100-avx512-2026-10-23-cfe5e5d87aaf'
PRIOR_ID = OLD_ID + '-mount-retry1'
SECOND_ID = OLD_ID + '-mount-retry2'
MISSED_ID = OLD_ID + '-node-retry1'
HELD_ID = OLD_ID + '-node-retry2'
NEW_ID = OLD_ID + '-node-retry3'
EXCLUDED_NODE = '4v100n01'
DIAGNOSTIC = ROOT / 'maintenance/cp2k-readonly-step-1914645-20261024'
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
    header = '#!/usr/bin/python3 -I\n'
    constants = 'TASK = Path(' + repr(str(task)) + ')\n'
    return header + '''import hashlib
import json
from pathlib import Path
import subprocess
import sys
''' + constants + '''
expected = ['--parsable', str(TASK / 'job.sbatch')]
received = sys.argv[1:]
if received != expected:
    raise ValueError('restricted sbatch wrapper received unexpected arguments')
executed = ['/usr/bin/sbatch', '--hold', '--exclude=4v100n01', '--cpus-per-task=8', *received]
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
            or request['controller'] != str(CONTROL) or request['target'] != '4v100-avx512'
            or request['dependencies_run'] != OLD_ID + '-dependencies'):
        raise ValueError('original source/recipe/checkpoint differs')
    state = subprocess.check_output(['sacct', '-X', '-nP', '-j', '1914241',
                                     '-o', 'JobIDRaw,State,ExitCode'], text=True).strip()
    if state != '1914241|FAILED|255:0':
        raise ValueError('original failed allocation differs: ' + state)
    log = (old / 'results/slurm-1914241.log').read_text()
    if ('CP2K_EXACT_DEPENDENCY_CHECKPOINT_CLONED' not in log or
            'fuse2fs failed to mount' not in log or 'in 10s' not in log):
        raise ValueError('not the reviewed pre-build mount timeout')
    if (old / 'artifact.path').exists():
        raise ValueError('original attempt unexpectedly has an artifact')
    preparation = json.loads((old / 'results/overlay-workdir-preparation.json').read_text())
    if preparation['status'] != 'prepared' or not preparation['source_unchanged']:
        raise ValueError('original clone did not finish its integrity checks')
    prior = ROOT / 'runs' / PRIOR_ID
    prior_request = json.loads((prior / 'request.json').read_text())
    prior_identity = prior_request['identity']
    if (prior_identity != identity or prior_request['controller'] != str(CONTROL)
            or prior_request['dependencies_run'] != request['dependencies_run']
            or (prior / 'job.id').read_text().strip() != '1914645'):
        raise ValueError('prior retry identity/controller/checkpoint differs')
    prior_accounting = subprocess.check_output(['sacct', '-X', '-nP', '-j', '1914645',
        '-o', 'JobIDRaw,State,ExitCode'], text=True).strip()
    if prior_accounting != '1914645|CANCELLED by 1478400690|0:0':
        raise ValueError('prior retry cancellation differs: ' + prior_accounting)
    diagnostic = json.loads((DIAGNOSTIC / 'diagnostic.json').read_text())
    diagnostic_stdout = (DIAGNOSTIC / 'stdout.json').read_text()
    diagnostic_stderr = (DIAGNOSTIC / 'stderr.txt').read_text()
    if ('Make sure per-GPU CPU logical core number is 8 for 4V100 partition' not in diagnostic_stdout
            or 'STEP 1914645.0' not in diagnostic_stderr
            or diagnostic['returncode'] != 143
            or '--jobid=1914645' not in diagnostic['argv']
            or '--cpus-per-task=1' not in diagnostic['argv']
            or digest(DIAGNOSTIC / 'stdout.json') != diagnostic['stdout_sha256']
            or digest(DIAGNOSTIC / 'stderr.txt') != diagnostic['stderr_sha256']
            or digest(DIAGNOSTIC / 'observe_processes.py') != diagnostic['script_sha256']):
        raise ValueError('diagnostic cancellation evidence differs')
    prior_log = (prior / 'results/slurm-1914645.log').read_text()
    if ('CP2K_EXACT_DEPENDENCY_CHECKPOINT_CLONED' not in prior_log
            or 'fuse2fs mount took an unexpectedly long time: 2.125s' not in prior_log
            or 'JOB 1914645 ON 4v100n01 CANCELLED' not in prior_log
            or 'Initialized empty Git repository' in prior_log
            or 'CP2K_APPLICATION_COMPILER' in prior_log
            or (prior / 'artifact.path').exists()):
        raise ValueError('prior retry did not stop at the reviewed pre-application state')
    prior_preparation = json.loads((prior / 'results/overlay-workdir-preparation.json').read_text())
    if prior_preparation['status'] != 'prepared' or not prior_preparation['source_unchanged']:
        raise ValueError('prior retry clone did not finish its integrity checks')
    second = ROOT / 'runs' / SECOND_ID
    second_request = json.loads((second / 'request.json').read_text())
    if (second_request['identity'] != identity or second_request['controller'] != str(CONTROL)
            or second_request['dependencies_run'] != request['dependencies_run']
            or (second / 'job.id').read_text().strip() != '1914931'):
        raise ValueError('second retry identity/controller/checkpoint differs')
    second_accounting = subprocess.check_output(['sacct', '-X', '-nP', '-j', '1914931',
        '-o', 'JobIDRaw,State,ExitCode,NodeList'], text=True).strip()
    if second_accounting != '1914931|FAILED|255:0|4v100n01':
        raise ValueError('second retry mount failure differs: ' + second_accounting)
    second_log = (second / 'results/slurm-1914931.log').read_text()
    if ('CP2K_EXACT_DEPENDENCY_CHECKPOINT_CLONED' not in second_log
            or 'fuse2fs failed to mount' not in second_log or 'in 10s' not in second_log
            or 'Initialized empty Git repository' in second_log
            or 'CP2K_APPLICATION_COMPILER' in second_log
            or (second / 'artifact.path').exists()):
        raise ValueError('second retry did not stop at the reviewed mount timeout')
    second_preparation = json.loads((second / 'results/overlay-workdir-preparation.json').read_text())
    if second_preparation['status'] != 'prepared' or not second_preparation['source_unchanged']:
        raise ValueError('second retry clone did not finish its integrity checks')
    missed = ROOT / 'runs' / MISSED_ID
    missed_receipt = json.loads((missed / 'results/retry-submitted.json').read_text())
    missed_request = json.loads((missed / 'request.json').read_text())
    missed_cancel = json.loads((missed / 'results/exclusion-cancelled.json').read_text())
    if (missed_receipt['job_id'] != '1915039' or missed_receipt['excluded_node_recorded'] is not False
            or missed_request['identity'] != identity or missed_request['controller'] != str(CONTROL)
            or missed_request['dependencies_run'] != request['dependencies_run']
            or missed_cancel['argv'] != ['scancel', '1915039'] or missed_cancel['returncode'] != 0
            or 'ExcNodeList=(null)' not in missed_cancel['allocation_before']
            or 'NodeList=4v100n01 ' not in missed_cancel['allocation_before']):
        raise ValueError('cancelled unsupported-environment attempt differs')
    missed_accounting = subprocess.check_output(['sacct', '-X', '-nP', '-j', '1915039',
        '-o', 'JobIDRaw,State,ExitCode,NodeList'], text=True).strip()
    if missed_accounting != '1915039|CANCELLED by 1478400690|0:0|4v100n01':
        raise ValueError('unsupported-environment attempt is not cancelled: ' + missed_accounting)
    held = ROOT / 'runs' / HELD_ID
    held_receipt = json.loads((held / 'results/retry-submitted.json').read_text())
    held_request = json.loads((held / 'request.json').read_text())
    held_accounting = subprocess.check_output(['sacct', '-X', '-nP', '-j', '1915163',
        '-o', 'JobIDRaw,State,ExitCode,Elapsed,NodeList'], text=True).strip()
    if (held_request['identity'] != identity or held_request['controller'] != str(CONTROL)
            or held_request['dependencies_run'] != request['dependencies_run']
            or held_receipt['job_id'] != '1915163'
            or held_receipt['guard_cancellation']['argv'] != ['scancel', '1915163']
            or held_receipt['guard_cancellation']['returncode'] != 0
            or not held_receipt['original_small_files_unchanged']
            or not held_receipt['excluded_node_recorded']
            or held_receipt['held_allocation_checks']['CpusPerTres'] is not False
            or any(not value for key, value in held_receipt['held_allocation_checks'].items()
                   if key != 'CpusPerTres')
            or held_accounting != '1915163|CANCELLED by 1478400690|0:0|00:00:00|None assigned'):
        raise ValueError('held pre-scheduling CPU-field cancellation differs')
    availability = subprocess.check_output(['sinfo', '-N', '-h', '-p', '4V100',
        '-o', '%N|%T|%C|%G'], text=True)
    candidates = []
    for line in availability.splitlines():
        node, node_state, cpus, gpus = line.split('|')
        idle_cpus = int(cpus.split('/')[1])
        if node != EXCLUDED_NODE and node_state in ('mixed', 'idle') and idle_cpus >= 8:
            candidates.append(node)
    if not candidates:
        raise ValueError('no other healthy 4V100 node currently reports at least eight idle CPUs')
    updates = old / 'input/cp2k-updates'
    archives = {path.name: digest(path) for path in updates.iterdir()}
    tracked = [old / name for name in ('request.json', 'job.id', 'job.sbatch',
               'results/slurm-1914241.log', 'results/overlay-workdir-preparation.json')]
    deps = ROOT / 'runs' / request['dependencies_run']
    tracked += [deps / name for name in ('request.json', 'job.id', 'job.sbatch', 'results/dependency-stage.json')]
    tracked += list(updates.iterdir())
    tracked += [prior / name for name in ('request.json', 'job.id', 'job.sbatch',
        'input/retry-plan.json', 'input/submission-attempt.json', 'results/retry-submitted.json',
        'results/slurm-1914645.log', 'results/overlay-workdir-preparation.json',
        'results/manual-monitor.log', 'results/status.json')]
    tracked += [DIAGNOSTIC / name for name in ('diagnostic.json', 'stdout.json', 'stderr.txt',
                                             'observe_processes.py')]
    tracked += [second / name for name in ('request.json', 'job.id', 'job.sbatch',
        'input/retry-plan.json', 'input/submission-attempt.json', 'results/retry-submitted.json',
        'results/slurm-1914931.log', 'results/overlay-workdir-preparation.json',
        'results/manual-monitor.log', 'results/status.json')]
    tracked += [missed / name for name in ('request.json', 'job.id', 'job.sbatch',
        'input/retry-plan.json', 'input/submission-attempt.json', 'results/retry-submitted.json',
        'results/slurm-1915039.log', 'results/submission-allocation.txt',
        'results/exclusion-cancellation-attempt.json', 'results/exclusion-cancelled.json')]
    tracked += [held / name for name in ('request.json', 'job.id', 'job.sbatch',
        'input/retry-plan.json', 'input/submission-attempt.json', 'input/submission-bin/sbatch',
        'results/retry-submitted.json', 'results/submission-allocation.txt',
        'results/sbatch-wrapper-call.json', 'results/sbatch-wrapper-result.json')]
    control_hashes = {str(path.relative_to(CONTROL)): digest(path)
                      for path in CONTROL.rglob('*') if path.is_file() and '__pycache__' not in path.parts}
    tracked += [CONTROL / name for name in control_hashes]
    observed = {str(path): digest(path) for path in tracked}
    env = {key: value for key, value in os.environ.items() if not key.startswith(('PYTHON', 'SBATCH_'))}
    env.update(SAI_SOFTWARE_ROOT=str(ROOT), PYTHONDONTWRITEBYTECODE='1')
    cli = ['/usr/bin/python3', '-B', '-s', str(CONTROL / 'software_controller.py')]
    computed = subprocess.check_output([*cli, 'identity', 'cp2k', SOURCE_SHA,
        identity['source_version'], '4v100-avx512', '--track', 'development', '--source-ref', 'master'],
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
    argv = [*cli, 'submit', 'cp2k', NEW_ID, SOURCE_SHA, identity['source_version'], '4v100-avx512',
        '--track', 'development', '--source-ref', 'master', '--dependencies-run', request['dependencies_run'],
        '--overlay-mb', str(request['overlay_mb']), '--jobs', str(request['jobs']),
        '--minutes', str(request['minutes'])]
    plan = dict(purpose=__doc__, prepared_at=datetime.now(timezone.utc).isoformat(),
        original_run=OLD_ID, original_job='1914241', new_run=NEW_ID, identity=identity,
        held_cpu_guard_run=HELD_ID, held_cpu_guard_job='1915163',
        held_cpu_guard_accounting=held_accounting,
        cpu_resource_override=dict(option='--cpus-per-task=8', ntasks=1, build_jobs=request['jobs'],
            reason='held jobs have not received scheduler CPU-per-GPU expansion; request eight logical CPUs explicitly before verifying and releasing'),
        unsupported_environment_run=MISSED_ID, unsupported_environment_job='1915039',
        unsupported_environment_accounting=missed_accounting,
        unsupported_environment_reason='SBATCH_EXCLUDE is not supported by this sbatch; postcheck rejected null ExcNodeList and only this newly submitted same-node allocation was explicitly cancelled',
        second_retry_run=SECOND_ID, second_retry_job='1914931', second_retry_accounting=second_accounting,
        second_retry_overlay_reused=False, excluded_node=EXCLUDED_NODE,
        submission_wrapper=dict(path=str(wrapper), sha256=digest(wrapper), injected_args=['--hold', '--exclude=' + EXCLUDED_NODE, '--cpus-per-task=8']),
        node_availability=availability, healthy_other_nodes_with_idle_cpus=candidates,
        node_retry_reason='two real pre-entry fuse2fs 10-second mount timeouts on 4v100n01; exclude that node without changing controller, recipe, driver, original jobs or checkpoint; root cause remains unproven',
        prior_retry_run=PRIOR_ID, prior_retry_job='1914645', prior_retry_accounting=prior_accounting,
        diagnostic_reason='agent diagnostic requested one CPU per task, violating SAI eight logical CPUs per GPU; site hook cancelled original allocation; not a compilation or mount failure',
        diagnostic_record=diagnostic, prior_retry_overlay_reused=False,
        diagnostic_policy_stdout=diagnostic_stdout, diagnostic_step_stderr=diagnostic_stderr,
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
        argv=argv, submission_wrapper=dict(path=str(wrapper), sha256=digest(wrapper), injected_args=['--hold', '--exclude=' + EXCLUDED_NODE, '--cpus-per-task=8']),
        rule='one submission only; inspect retained run on any interruption'))
    with (new / 'results/submit.stdout').open('x') as out, (new / 'results/submit.stderr').open('x') as err:
        result = subprocess.run(argv, cwd=ROOT, env=env, stdout=out, stderr=err)
    receipt = dict(returncode=result.returncode, run_id=NEW_ID,
                   original_small_files_unchanged=all(digest(Path(name)) == value for name, value in observed.items()))
    if result.returncode == 0:
        receipt['job_id'] = (new / 'job.id').read_text().strip()
        fresh = json.loads((new / 'request.json').read_text())
        if fresh['identity'] != identity or fresh['dependencies_run'] != request['dependencies_run']:
            raise ValueError('submitted retry identity/checkpoint differs')
        receipt['request_sha256'] = digest(new / 'request.json')
        receipt['job_script_sha256'] = digest(new / 'job.sbatch')
        wrapper_call = json.loads((new / 'results/sbatch-wrapper-call.json').read_text())
        wrapper_result = json.loads((new / 'results/sbatch-wrapper-result.json').read_text())
        if (wrapper_result['returncode'] != 0 or wrapper_result['job_id'] != receipt['job_id']
                or wrapper_call['received_argv'] != ['--parsable', str(new / 'job.sbatch')]
                or wrapper_call['executed_argv'] != ['/usr/bin/sbatch', '--hold',
                    '--exclude=' + EXCLUDED_NODE, '--cpus-per-task=8', '--parsable', str(new / 'job.sbatch')]
                or wrapper_call['wrapper_sha256'] != digest(wrapper)
                or wrapper_call['job_script_sha256'] != digest(new / 'job.sbatch')):
            raise ValueError('held submission wrapper record differs; inspect held job, do not resubmit')
        allocation = subprocess.check_output(['scontrol', 'show', 'job', '-o', receipt['job_id']], text=True)
        receipt['submission_allocation'] = allocation
        fields = dict(item.split('=', 1) for item in allocation.split() if '=' in item)
        expected_fields = dict(JobId=receipt['job_id'], JobState='PENDING', Reason='JobHeldUser',
            Account='proj-sai-hpc-software', Partition='4V100', ExcNodeList=EXCLUDED_NODE,
            WorkDir=str(new), Command=str(new / 'job.sbatch'), TimeLimit='04:00:00',
            QOS='flood-1o2gpu', NumTasks='1', NumCPUs='8', TresPerNode='gres/gpu:1')
        expected_fields['CPUs/Task'] = '8'
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
            raise ValueError('release did not succeed; inspect existing held job, do not resubmit')
    write_json(new / 'results/retry-submitted.json', receipt)
    print(json.dumps(receipt, sort_keys=True), flush=True)
    if result.returncode or not receipt['original_small_files_unchanged']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
