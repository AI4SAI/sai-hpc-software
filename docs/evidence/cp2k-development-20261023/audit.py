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
RECOVERIES = {'4v100-avx512': ('-mount-retry1', '1914241')}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    require(path.is_file() and path.resolve() == path,
            'noncanonical evidence: ' + str(path))
    return json.loads(path.read_text())


def audit(target):
    require(target in TARGETS, 'unsupported CP2K target')
    base_run_id = WORKFLOW + '-1-development-' + target + '-2026-10-23-' + SOURCE[:12]
    run_id = base_run_id + RECOVERIES.get(target, ('', ''))[0]
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
    recovery = None
    if target in RECOVERIES:
        old = ROOT / 'runs' / base_run_id
        old_job = RECOVERIES[target][1]
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
        require(plan['original_run'] == base_run_id and plan['new_run'] == run_id
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
        recovery = dict(original_run=base_run_id, original_job=old_job,
                        retry_plan=plan, retry_receipt=retry,
                        original_workflow_success_claimed=False)
    requested_jobs = list(jobs) + ([recovery['original_job']] if recovery else [])
    accounting = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', ','.join(requested_jobs),
        '--format=JobIDRaw,State,ExitCode,Partition,Account,WorkDir,Elapsed,NodeList'], text=True)
    rows = [line.split('|') for line in accounting.splitlines() if line.split('|')[0] in jobs]
    require(len(rows) == 3 and len({row[0] for row in rows}) == 3,
            'missing or ambiguous dependency/build/science accounting')
    for row in rows:
        require(row[1:6] == ['COMPLETED', '0:0', identity['partition'],
                            'proj-sai-hpc-software', str(jobs[row[0]])],
                'allocation differs from accepted job/project/partition: ' + row[0])
    if recovery:
        failed_rows = [line.split('|') for line in accounting.splitlines()
                       if line.split('|')[0] == recovery['original_job']]
        require(len(failed_rows) == 1 and failed_rows[0][1:6] ==
                ['FAILED', '255:0', identity['partition'], 'proj-sai-hpc-software',
                 str(ROOT / 'runs' / base_run_id)],
                'original failed allocation was not preserved')
        recovery['original_accounting'] = failed_rows[0]
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
