#!/usr/bin/env python3
"""Read-only revalidation of a published CP2K development delivery on SAI.

Run with Python -I -B on the login node, supplying this script on stdin.
The original controller reparses raw science and checks the final SIF, native
export and module. No build, acceptance or publication record is rewritten.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path('/org/org-open-sai/proj-sai-hpc-software')
SOURCE = 'cfe5e5d87aaf302953f14eb43587bef2411a8fb2'
CONTROL_SHA = '10992f818af71543bb39cb81e0f60c9f7ede9535'
WORKFLOW = '39983165813'
TARGETS = ('dsprhbm', '4v100-avx512', '16v100-avx2', '8v100v0-avx512')
# Keep the failed workflow allocation separate from its fresh application retry.
# The retry consumes the original sealed dependencies and frozen controller.
RECOVERIES = {
    '4v100-avx512': dict(suffix='-node-retry4', original_job='1914241',
        excluded_node='4v100n01', allocated_cpus=8,
        previous=(('prior_retry', '-mount-retry1', '1914645', 'CANCELLED by 1478400690', '0:0', '4v100n01'),
                  ('second_retry', '-mount-retry2', '1914931', 'FAILED', '255:0', '4v100n01'),
                  ('unsupported_environment', '-node-retry1', '1915039', 'CANCELLED by 1478400690', '0:0', '4v100n01'),
                  ('held_cpu_guard', '-node-retry2', '1915163', 'CANCELLED by 1478400690', '0:0', 'None assigned'))),
    '8v100v0-avx512': dict(suffix='-node-retry1', original_job='1914617',
        excluded_node='8v100v0n02', allocated_cpus=6, previous=()),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    require(path.is_file() and path.resolve() == path,
            'noncanonical evidence: ' + str(path))
    return json.loads(path.read_text())


def audit_recovery(target, base_run_id, build, request, control, checksum):
    configuration = RECOVERIES[target]
    identity = request['identity']
    old = ROOT / 'runs' / base_run_id
    old_job = configuration['original_job']
    old_request = read(old / 'request.json')
    retry = read(build / 'results/retry-submitted.json')
    plan = read(build / 'input/retry-plan.json')
    require((old / 'job.id').read_text().strip() == old_job
            and old_request['identity'] == identity
            and old_request['controller'] == str(control)
            and old_request['dependencies_run'] == request['dependencies_run'],
            'original failed application identity changed')
    require(retry['job_id'] == (build / 'job.id').read_text().strip()
            and retry['returncode'] == 0 and retry['original_small_files_unchanged']
            and retry['request_sha256'] == checksum(build / 'request.json')
            and retry['job_script_sha256'] == checksum(build / 'job.sbatch'),
            'retry receipt does not describe the final application')
    require(plan['original_run'] == base_run_id and plan['new_run'] == build.name
            and plan['original_job'] == old_job and plan['identity'] == identity
            and plan['dependencies_run'] == request['dependencies_run']
            and plan['controller'] == str(control)
            and plan['original_failed_overlay_reused'] is False
            and plan['mount_timeout_changed'] is False
            and plan['original_workflow_success_claimed'] is False,
            'retry plan differs from the reviewed recovery')
    for name, expected in plan['original_small_file_sha256'].items():
        path = Path(name)
        require(path.is_relative_to(ROOT) and path.is_file() and path.resolve() == path
                and checksum(path) == expected, 'original recovery input changed: ' + name)
    history = [dict(run_id=base_run_id, job=old_job, state='FAILED', exit_code='255:0',
                    node=configuration['excluded_node'])]
    for key, suffix, job, state, exit_code, node in configuration['previous']:
        historical_run = base_run_id + suffix
        historical = ROOT / 'runs' / historical_run
        historical_request = read(historical / 'request.json')
        require(plan[key + '_run'] == historical_run and plan[key + '_job'] == job
                and (historical / 'job.id').read_text().strip() == job
                and historical_request['identity'] == identity
                and historical_request['controller'] == str(control)
                and historical_request['dependencies_run'] == request['dependencies_run'],
                'historical recovery changed: ' + job)
        history.append(dict(run_id=historical_run, job=job, state=state, exit_code=exit_code, node=node))
    if target == '4v100-avx512':
        require(plan['diagnostic_record']['returncode'] == 143
                and '--jobid=1914645' in plan['diagnostic_record']['argv']
                and '--cpus-per-task=1' in plan['diagnostic_record']['argv']
                and 'Make sure per-GPU CPU logical core number is 8 for 4V100 partition'
                    in plan['diagnostic_policy_stdout']
                and plan['prior_retry_overlay_reused'] is False
                and plan['second_retry_overlay_reused'] is False,
                'diagnostic cancellation or nonreuse evidence changed')
        rejected = ROOT / 'runs' / (base_run_id + '-node-retry3')
        rejection = read(rejected / 'results/sbatch-wrapper-result.json')
        require(plan['cpu_policy_rejected_run'] == rejected.name
                and plan['cpu_policy_rejection'] == rejection
                and rejection['returncode'] == 1 and rejection['job_id'] == ''
                and rejection['stdout'] == ''
                and 'SAI GPU policy: CPUs follow GPU count; omit --cpus-per-task / CPUsPerTask.'
                    in rejection['stderr']
                and not (rejected / 'job.id').exists() and not (rejected / 'request.json').exists(),
                'explicit CPU submission rejection was not preserved')
    excluded = configuration['excluded_node']
    require(plan['excluded_node'] == excluded and retry['excluded_node_recorded'] is True
            and retry['release']['argv'] == ['scontrol', 'release', retry['job_id']]
            and retry['release']['returncode'] == 0,
            'explicit node exclusion or verified release is missing')
    expected_held = dict(JobId=retry['job_id'], JobState='PENDING', Reason='JobHeldUser',
        Account='proj-sai-hpc-software', Partition=identity['partition'], ExcNodeList=excluded,
        WorkDir=str(build), Command=str(build / 'job.sbatch'), TimeLimit='04:00:00',
        QOS='flood-1o2gpu', NumTasks='1', TresPerNode='gres/gpu:1')
    held = dict(item.split('=', 1) for item in retry['submission_allocation'].split() if '=' in item)
    require(retry['held_allocation_checks'] == {key: True for key in expected_held}
            and all(held.get(key) == value for key, value in expected_held.items()),
            'held allocation did not pass the reviewed scheduling checks')
    # GPU CPUs are assigned by the SAI scheduler. Check the resulting allocation
    # below, not the placeholder CPU fields reported while the job is held.
    policy = plan['cpu_allocation_policy']
    require(policy['explicit_cpu_option'] is False
            and policy['expected_running_cpus'] == configuration['allocated_cpus']
            and policy['build_jobs'] == request['jobs'], 'CPU allocation policy changed')
    wrapper = build / 'input/submission-bin/sbatch'
    wrapper_call = read(build / 'results/sbatch-wrapper-call.json')
    wrapper_result = read(build / 'results/sbatch-wrapper-result.json')
    require(plan['submission_wrapper'] == dict(path=str(wrapper), sha256=checksum(wrapper),
                injected_args=['--hold', '--exclude=' + excluded])
            and wrapper_call['received_argv'] == ['--parsable', str(build / 'job.sbatch')]
            and wrapper_call['executed_argv'] == ['/usr/bin/sbatch', '--hold',
                '--exclude=' + excluded, '--parsable', str(build / 'job.sbatch')]
            and wrapper_call['wrapper_sha256'] == checksum(wrapper)
            and wrapper_call['job_script_sha256'] == checksum(build / 'job.sbatch')
            and wrapper_result['returncode'] == 0 and wrapper_result['job_id'] == retry['job_id'],
            'submission wrapper or scheduler invocation changed')
    return dict(original_run=base_run_id, original_job=old_job, retry_plan=plan,
                retry_receipt=retry, prior_allocations=history, wrapper_call=wrapper_call,
                wrapper_result=wrapper_result, original_workflow_success_claimed=False)


def audit(target):
    require(target in TARGETS, 'unsupported CP2K target')
    base_run_id = WORKFLOW + '-1-development-' + target + '-2026-10-23-' + SOURCE[:12]
    run_id = base_run_id + RECOVERIES.get(target, {}).get('suffix', '')
    build = ROOT / 'runs' / run_id
    request = read(build / 'request.json')
    control = ROOT / 'controller' / CONTROL_SHA / base_run_id
    require(request['controller'] == str(control) and control.resolve() == control,
            'wrong frozen controller')
    os.environ['SAI_SOFTWARE_ROOT'] = str(ROOT)
    sys.path.insert(0, str(control))
    from source_cache import checksum
    import software_controller as original
    from delivery_layout import artifact_path, validate_record

    identity = validate_record(request)
    require(identity['source_sha'] == SOURCE and identity['source_ref'] == 'master'
            and identity['track'] == 'development' and identity['software'] == 'cp2k'
            and identity['target'] == target, 'wrong source, channel or target')
    require((build / 'job.sbatch').read_text() == original.render_job(argparse.Namespace(**request)),
            'frozen build script changed')
    artifact = artifact_path(ROOT, identity, run_id)
    manifest = read(artifact.with_suffix('.json'))
    original.validate_candidate(manifest, artifact, published=True)

    dependency = ROOT / 'runs' / request['dependencies_run']
    require(dependency == ROOT / 'runs' / (base_run_id + '-dependencies'),
            'build used a different dependency run')
    dependency_receipt = read(dependency / 'results/dependency-stage.json')
    require(dependency_receipt['identity'] == identity, 'dependency identity differs')
    jobs = {(build / 'job.id').read_text().strip(): build,
            (dependency / 'job.id').read_text().strip(): dependency}
    proof = manifest['cp2k_science']
    stage = ROOT / 'runtime-tests' / proof['run_id']
    jobs[proof['job']] = stage
    report = read(stage / 'results/evidence.json')
    recovery = (audit_recovery(target, base_run_id, build, request, control, checksum)
                if target in RECOVERIES else None)
    requested_jobs = list(jobs) + ([row['job'] for row in recovery['prior_allocations']]
                                   if recovery else [])
    accounting = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', ','.join(requested_jobs),
        '--format=JobIDRaw,State,ExitCode,Partition,Account,WorkDir,Elapsed,NodeList,AllocCPUS'], text=True)
    rows = [line.split('|') for line in accounting.splitlines() if line.split('|')[0] in jobs]
    require(len(rows) == 3 and len({row[0] for row in rows}) == 3,
            'missing or ambiguous dependency/build/science accounting')
    for row in rows:
        require(row[1:6] == ['COMPLETED', '0:0', identity['partition'],
                            'proj-sai-hpc-software', str(jobs[row[0]])],
                'allocation differs from accepted job/project/partition: ' + row[0])
    if recovery:
        for historical in recovery['prior_allocations']:
            prior_rows = [line.split('|') for line in accounting.splitlines()
                          if line.split('|')[0] == historical['job']]
            require(len(prior_rows) == 1 and prior_rows[0][1:6] ==
                    [historical['state'], historical['exit_code'], identity['partition'],
                     'proj-sai-hpc-software', str(ROOT / 'runs' / historical['run_id'])]
                    and prior_rows[0][7] == historical['node'],
                    'historical allocation was not preserved: ' + historical['job'])
            if historical['job'] == '1915163':
                require(prior_rows[0][6] == '00:00:00', 'held cancellation unexpectedly ran')
            historical['accounting'] = prior_rows[0]
        successful_build = next(row for row in rows if row[0] == recovery['retry_receipt']['job_id'])
        expected = RECOVERIES[target]
        require(successful_build[7].startswith(identity['partition'].lower() + 'n')
                and successful_build[7] != expected['excluded_node']
                and int(successful_build[8]) == expected['allocated_cpus'],
                'recovered application node or scheduler-assigned CPUs differ')
    return dict(schema=1, audited_at=datetime.now(timezone.utc).isoformat(),
        workflow='https://github.com/AI4SAI/sai-hpc-software/actions/runs/' + WORKFLOW,
        run_id=run_id, identity=identity, frozen_controller=str(control),
        build_request=request, build_status=read(build / 'results/status.json'),
        build_script_sha256=checksum(build / 'job.sbatch'), artifact=str(artifact),
        artifact_sha256=manifest['sha256'], artifact_bytes=artifact.stat().st_size,
        manifest_sha256=checksum(artifact.with_suffix('.json')), manifest=manifest,
        dependency_receipt=dependency_receipt,
        dependency_receipt_sha256=checksum(dependency / 'results/dependency-stage.json'),
        accounting=rows, recovery=recovery,
        science_request=read(stage / 'request.json'), science=report,
        science_evidence_sha256=checksum(stage / 'results/evidence.json'),
        verified=True, scientific_verified=True, project_catalog_published=True,
        native_export_verified=True, native_runtime_verified=False, installed_to_opt=False,
        scope='Final SIF, raw scientific evidence, native export and module revalidated '
              'with the original controller. Dependency receipt is retained, without '
              'another full overlay hash. LibXC supplement and physical installation '
              'are separate evidence; no historical performance comparison is claimed.')


if __name__ == '__main__':
    print(json.dumps(audit(sys.argv[1]), sort_keys=True, indent=2))
