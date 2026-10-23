#!/usr/bin/env python3
"""Read-only audit of one published ABACUS development delivery on SAI.

Run on the login node with Python -I -B; source is supplied on stdin. This
loads the delivery's original controller and does not submit or publish work.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path('/org/org-open-sai/proj-sai-hpc-software')
SOURCE = '370ba685e6707b3625c5580ff9d02ef0afb248e8'
CONTROL_SHA = '23bb4c1f13e02a87f010db74189341d8fc60f058'
TARGETS = ('dsprhbm', '4v100-avx512', '16v100-avx2', '8v100v0-avx512')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    require(path.is_file() and path.resolve() == path, 'noncanonical evidence: ' + str(path))
    return json.loads(path.read_text())


def audit(target):
    require(target in TARGETS, 'unsupported ABACUS target')
    run_id = '39971072923-1-development-' + target + '-2026-10-23-' + SOURCE[:12]
    build = ROOT / 'runs' / run_id
    request = read(build / 'request.json')
    control = ROOT / 'controller' / CONTROL_SHA / run_id
    require(request['controller'] == str(control) and control.resolve() == control, 'wrong frozen controller')
    os.environ['SAI_SOFTWARE_ROOT'] = str(ROOT)
    sys.path.insert(0, str(control))
    from source_cache import checksum
    import software_controller as original
    from delivery_layout import artifact_path, validate_record

    identity = validate_record(request)
    require(identity['source_sha'] == SOURCE and identity['source_ref'] == 'develop'
            and identity['track'] == 'development' and identity['software'] == 'abacus'
            and identity['target'] == target, 'wrong source, channel or target')
    require((build / 'job.sbatch').read_text() == original.render_job(argparse.Namespace(**request)),
            'frozen build script changed')
    artifact = artifact_path(ROOT, identity, run_id)
    manifest = read(artifact.with_suffix('.json'))
    # Reparse raw numerical and GPU traces, enforce each performance gate,
    # hash the SIF, and read back every exported file/link and module byte.
    original.validate_candidate(manifest, artifact, published=True)

    jobs = {(build / 'job.id').read_text().strip(): build}
    evidence, summary = {}, {}
    for name in original.required_acceptance('abacus', target):
        proof = manifest[name]
        stage = ROOT / 'runtime-tests' / proof['run_id']
        jobs[proof['job']] = stage
        report = read(stage / 'results/evidence.json')
        evidence[name] = dict(directory=str(stage), request=read(stage / 'request.json'),
            status=read(stage / 'results/status.json'), evidence=report,
            evidence_sha256=checksum(stage / 'results/evidence.json'),
            job_script_sha256=checksum(stage / 'job.sbatch'))
        summary[name] = {key: report[key] for key in (
            'job', 'ranks', 'nodes', 'final_energy_ev', 'nccl_collective', 'cusolvermp_eigensolve',
            'benchmark_case', 'case_device', 'energy_spread_ev', 'energy_reference', 'statistics') if key in report}
    accounting = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', ','.join(jobs),
        '--format=JobIDRaw,State,ExitCode,Partition,Account,WorkDir,Elapsed,NodeList'], text=True)
    rows = [line.split('|') for line in accounting.splitlines() if line.split('|')[0] in jobs]
    require(len(rows) == len(jobs) and len({row[0] for row in rows}) == len(jobs), 'missing/ambiguous accounting')
    for row in rows:
        require(row[1:6] == ['COMPLETED', '0:0', identity['partition'], 'proj-sai-hpc-software', str(jobs[row[0]])],
                'allocation differs from accepted job/project/partition: ' + row[0])
    return dict(schema=1, audited_at=datetime.now(timezone.utc).isoformat(),
        workflow='https://github.com/AI4SAI/sai-hpc-software/actions/runs/39971072923',
        run_id=run_id, identity=identity, frozen_controller=str(control),
        build_request=request, build_status=read(build / 'results/status.json'),
        build_script_sha256=checksum(build / 'job.sbatch'), artifact=str(artifact),
        artifact_sha256=manifest['sha256'], artifact_bytes=artifact.stat().st_size,
        manifest_sha256=checksum(artifact.with_suffix('.json')), manifest=manifest,
        accounting=rows, scientific_summary=summary, acceptance=evidence,
        verified=True, scientific_verified=True, performance_verified=True,
        project_catalog_published=True, native_export_verified=True,
        native_runtime_verified=False, installed_to_opt=False,
        scope='Existing SIF and raw acceptance evidence revalidated with the original frozen controller; '
              'native export integrity verified; physical /opt installation/native execution is a separate step')


if __name__ == '__main__':
    print(json.dumps(audit(sys.argv[1]), sort_keys=True, indent=2))
