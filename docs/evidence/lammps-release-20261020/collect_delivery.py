#!/usr/bin/env python3
"""Summarize completed publication and public-module checks without rewriting them."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

PROJECT = Path('/home/stardust/sai-hpc-software')
ROOT = PROJECT / 'experimental/deepmd-lammps'
DIAG = PROJECT / 'diagnostics/lammps-release-20261020'
PACKAGE = PROJECT / 'deployment/lammps-30Sep2026'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    release = read(PACKAGE / 'release.json')
    output = dict(collected_at=datetime.now(timezone.utc).isoformat(),
                  release=release['version'], deepmd=release['deepmd_version'], deliveries=[])
    for item in release['deliveries']:
        build = item['build_run']
        results = ROOT / 'runs' / build / 'results'
        publication = results / 'publication.json'
        assert sha(publication) == item['publication_sha256']
        manifest = read(publication)
        recovery = read(results / 'publication-export-recovery.json')
        assert recovery['state'] == 'published-and-reverified'
        assert recovery['publication_sha256'] == sha(publication)
        science = manifest['acceptance']['science']
        performance = manifest['acceptance']['performance']
        task = ROOT / 'runtime-tests' / (build + '-published-module')
        check = read(task / 'results/evidence.json')
        job = (task / 'job.id').read_text().strip()
        assert check['passed'] and check['job'] == job and (task / 'results/module-unloaded').is_file()
        accounting = subprocess.check_output(['sacct', '-X', '-j', job, '-n', '-P',
                                              '-o', 'JobID,State,ExitCode,Elapsed,NodeList'], text=True).strip()
        assert accounting.split('|')[:3] == [job, 'COMPLETED', '0:0']
        for name, case in check['cases'].items():
            assert sha(task / name / 'stdout.txt') == case['stdout_sha256']
            assert sha(task / name / 'result.dump') == case['dump_sha256']
        row = dict(partition=item['partition'], build_run=build, artifact=manifest['artifact'],
                   artifact_sha256=manifest['artifact_sha256'], artifact_bytes=item['image_bytes'],
                   publication=str(publication), publication_sha256=sha(publication),
                   published=True, reverified=True, compiled_recipe_sha256=manifest['recipe_sha256'],
                   acceptance_recipe_sha256=manifest['acceptance_recipe_sha256'],
                   scientific=dict(run_id=science['run_id'], job=science['evidence']['job'],
                       passed=science['evidence']['passed'], complete_backends=science['evidence']['complete_backends'],
                       benchmarks=science['evidence']['benchmarks']),
                   performance=dict(run_id=performance['run_id'], scope=manifest['performance_scope'],
                       backends=manifest['performance_backends'],
                       reports={name: result['performance'] for name,result in performance['evidence']['reports'].items()}),
                   native_export=dict(directory=manifest['native_export']['directory'],
                       roundtrip_verified=manifest['native_export']['roundtrip_verified'],
                       verified_files=manifest['native_export']['verified_files']),
                   publication_recovery=recovery,
                   public_module=dict(evidence=check, accounting=accounting, unload_passed=True,
                       request_sha256=sha(task / 'request.json'), job_script_sha256=sha(task / 'job.sbatch'),
                       help_sha256=sha(task / 'results/help.txt')))
        output['deliveries'].append(row)
    log = DIAG / 'native-preflight.log'
    assert log.read_text().rstrip().endswith('LAMMPS_NATIVE_PREFLIGHT_PASSED')
    output['physical_deployment'] = dict(installed=False, preflight_passed=True, preflight_log=str(log),
        preflight_log_sha256=sha(log), package=str(PACKAGE), release_sha256=sha(PACKAGE / 'release.json'))
    path = DIAG / 'final-delivery.json'
    with path.open('x') as stream:
        json.dump(output, stream, sort_keys=True, indent=2)
        stream.write('\n')
    print(path)


if __name__ == '__main__':
    main()
