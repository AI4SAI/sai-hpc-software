"""Snapshot the fixed 2026-10-17 private-driver workflow without mounting images.

Run on SAI's login node. Historical CI validation and prior CPU samples retain
their own timestamps; only the Slurm/log/artifact snapshot is newly observed.
Use --output with a new filename under the project diagnostics home.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

root = Path('/home/stardust/sai-hpc-software')
diagnostics = root / 'diagnostics/cp2k-release-20261016'
workflow = '39032306213'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
if not args.output.resolve().is_relative_to(diagnostics):
    parser.error('keep the snapshot under the project diagnostics home')


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


records = []
for run in sorted((root / 'runs').glob(workflow + '-1-release-*-dependencies')):
    request = json.loads((run / 'request.json').read_text())
    job = request['job_id']
    log = run / 'results' / ('slurm-' + job + '.log')
    lines = log.read_text(errors='replace').splitlines() if log.exists() else []
    result = dict(run_id=run.name, job_id=job, identity=request['identity'],
                  controller=request['controller'], request_sha256=checksum(run / 'request.json'),
                  job_script_sha256=checksum(run / 'job.sbatch'),
                  accounting=subprocess.check_output(['sacct', '-X', '-nP', '-j', job,
                      '-o', 'JobIDRaw,Partition,State,Start,Elapsed,AllocCPUS,NodeList,ExitCode'], text=True).strip(),
                  log_tail=lines[-10:], dependency_sealed=(run / 'results/dependency-stage.json').is_file(),
                  artifact_path=(root / 'runs' / run.name.removesuffix('-dependencies') / 'artifact.path').read_text().strip()
                      if (root / 'runs' / run.name.removesuffix('-dependencies') / 'artifact.path').exists() else None)
    for name in ('image-driver.json', 'native-profile.json'):
        path = run / 'results' / name
        if path.exists():
            data = json.loads(path.read_text())
            result[name] = dict(sha256=checksum(path),
                               data=data.get('compiler_versions', data))
    result['completed_dependencies'] = [line for line in lines if line.startswith('[+] ')]
    seal = run / 'results/dependency-stage.json'
    if seal.exists():
        result['dependency_seal'] = dict(sha256=checksum(seal), data=json.loads(seal.read_text()))
    application = root / 'runs' / run.name.removesuffix('-dependencies')
    application_handle = application / 'job.id'
    if application_handle.exists():
        application_job = application_handle.read_text().strip()
        if not application_job.isdigit():
            raise ValueError('unexpected application job handle')
        application_log = application / 'results' / ('slurm-' + application_job + '.log')
        result['application'] = dict(job_id=application_job,
            accounting=subprocess.check_output(['sacct', '-X', '-nP', '-j', application_job,
                '-o', 'JobIDRaw,Partition,State,Start,Elapsed,AllocCPUS,NodeList,ExitCode'], text=True).strip(),
            log_tail=application_log.read_text(errors='replace').splitlines()[-10:] if application_log.exists() else [])
    records.append(result)

historical = []
for run in sorted((root / 'runs').glob('39018993951-1-release-dsprhbm-*')):
    handle = run / 'job.id'
    if not handle.exists():
        continue
    job = handle.read_text().strip()
    if not job.isdigit():
        raise ValueError('unexpected historical job handle')
    log = run / 'results' / ('slurm-' + job + '.log')
    historical.append(dict(run_id=run.name, job_id=job,
        accounting=subprocess.check_output(['sacct', '-X', '-nP', '-j', job,
            '-o', 'JobIDRaw,Partition,State,Start,Elapsed,AllocCPUS,NodeList,ExitCode'], text=True).strip(),
        log_tail=log.read_text(errors='replace').splitlines()[-8:] if log.exists() else [],
        dependency_sealed=(run / 'results/dependency-stage.json').is_file(),
        artifact_path=(run / 'artifact.path').read_text().strip() if (run / 'artifact.path').exists() else None))

samples = []
for filename in sorted(diagnostics.glob('cpu-io-*-production*-20261017.json')):
    data = json.loads(filename.read_text())
    cgroup = '/sys/fs/cgroup' + data['job_cgroup_prefixes'][0][1]
    def usage(key):
        return int(dict(line.split() for line in data[key][cgroup]['cpu.stat'].splitlines())['usage_usec'])
    cores = (usage('final_cgroups') - usage('initial_cgroups')) / 1e6 / data['sample_seconds']
    allocated = 12 if data['job_id'] == '1830995' else 8
    samples.append(dict(job_id=data['job_id'], checked_at=data['checked_at'], node=data['node'],
                        source=str(filename), source_sha256=checksum(filename),
                        sample_seconds=data['sample_seconds'], allocated_logical_cpus=allocated,
                        used_logical_cpus=cores, utilization_percent=100 * cores / allocated,
                        driver_processes=[p['command'] for p in data['final_processes'] if p['name'] == 'fuse2fs'],
                        compiler_process_count=sum(p['name'] in ('cc1', 'cc1plus', 'f951') for p in data['final_processes'])))

record = dict(schema=1, checked_at=datetime.now(timezone.utc).isoformat(),
              purpose='actual-cp2k-release-build-progress-not-release-acceptance',
              workflow_url='https://github.com/AI4SAI/sai-hpc-software/actions/runs/' + workflow,
              controller_sha='1e7d736e5884fe207460a3e6baf0aeddb5bdb4aa', builds=records, cpu_samples=samples,
              historical_dsprhbm_runs=historical,
              validation=dict(ci_total=709, ci_passed=707, ci_skipped=2,
                              ci_url='https://github.com/AI4SAI/sai-hpc-software/actions/runs/39032280292',
                              sai_focused_total=20, sai_focused_passed=20,
                              sai_log=str(diagnostics / 'image-driver-focused-tests-20261017.log'),
                              sai_log_sha256=checksum(diagnostics / 'image-driver-focused-tests-20261017.log')),
              notes=['All build and temporary storage stays under the project home.',
                     'Old running jobs and controllers were preserved.',
                     'Cgroup samples include short-lived compiler CPU time.',
                     'Different dependency stages cannot establish whole-build speedup.'])
destination = args.output
with destination.open('x') as stream:
    stream.write(json.dumps(record, sort_keys=True, indent=2) + '\n')
print(json.dumps(dict(evidence=str(destination), sha256=checksum(destination),
                      states=[dict(job_id=row['job_id'], accounting=row['accounting']) for row in records],
                      cpu_samples=[{k: row[k] for k in ('job_id', 'checked_at', 'used_logical_cpus', 'utilization_percent')} for row in samples])))
