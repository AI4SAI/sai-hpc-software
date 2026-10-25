#!/usr/bin/env python3
"""Prepare independent final-SIF acceptance after all three build gates pass."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / 'controller'))
from sif_modules import install, load_snapshot

submission = json.loads((root / 'submission-r2.json').read_text())
public = Path('/home/stardust/sai-hpc-software')
snapshot = load_snapshot(public / 'sif-beta/runtimes/20261025-75ef5179f1e8/catalog.json')
snapshot['snapshot'] = '20261025'
app = snapshot['applications']['deepmd-kit']
snapshot['applications'] = {'deepmd-kit': app}
app['version'] = 'master-20261025-g558f56962775'
build_root = Path('/org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-standalone/runs')
previous = Path('/org/org-open-sai/proj-sai-hpc-software/runtime-tests/deepmd-ldconfig-20261025-r2')
statuses = []
for build in submission['builds']:
    run = build_root / build['run_id']
    response = subprocess.run(['env', 'PYTHONDONTWRITEBYTECODE=1', 'python3',
        submission['controller'] + '/deepmd_standalone.py', 'status', build['run_id']], text=True, capture_output=True)
    assert response.returncode == 1, response.stderr
    status = json.loads(response.stdout)
    assert status['build_verified'] and not status['scientific_verified']
    acceptance = build_root / build['run_id'].replace('-r2-', '-science-r3-')
    continuation = json.loads((acceptance / 'request.json').read_text())
    accepted = json.loads(subprocess.check_output(['env', 'PYTHONDONTWRITEBYTECODE=1', 'python3',
        continuation['controller'] + '/deepmd_acceptance.py', 'status', acceptance.name], text=True))
    assert accepted['scientific_verified'] and accepted['source']['artifact_sha256'] == status['artifact_sha256']
    statuses.append(dict(build=status, acceptance=accepted))
    identity = build['request']['delivery']['identity']
    partition = identity['partition']
    directory = root / partition
    directory.mkdir()
    shutil.copytree(root / 'control', directory / 'control')
    shutil.copytree(previous / partition / 'inference', directory / 'inference')
    (directory / 'old-checkpoints').mkdir()
    for descriptor in ('se_e2_a', 'dpa2'):
        shutil.copyfile(previous / partition / (descriptor + '-single') / 'model.ckpt-8.pt',
                        directory / 'old-checkpoints' / (descriptor + '.pt'))
    environment = (run / 'runtime/runtime-env.sh').read_text()
    row = app['partitions'][partition]
    row.update(identity=identity, image=str(run / 'candidate.sif'), sha256=status['artifact_sha256'],
               build_verified=True, scientific_verified=True, environment=environment,
               environment_sha256=hashlib.sha256(environment.encode()).hexdigest(),
               evidence=str(acceptance / 'results/status.json'),
               evidence_sha256=hashlib.sha256((acceptance / 'results/status.json').read_bytes()).hexdigest())
    shutil.copyfile(run / 'runtime/ddp-source-patch.json', directory / 'source-patch.json')
(root / 'build-status.json').write_text(json.dumps(statuses, indent=2) + '\n')
install(snapshot, root / 'bundle')
jobs = []
for partition in app['partitions']:
    shutil.copyfile(root / 'bundle/catalog.json', root / partition / 'catalog.json')
    qos = 'flood-1o2gpu' if partition == '16V100' else 'rush-1o2gpu'
    args = ['sbatch', '--parsable', '--account=proj-sai-hpc-software', '--chdir=' + str(root),
            '--nodes=1', '--ntasks=1', '--gpus-per-node=2', '--partition=' + partition, '--qos=' + qos,
            '--job-name=deepmd-ddp-' + partition, '--output=' + str(root / partition / 'slurm-%j.log'),
            str(root / 'control/run.sbatch')]
    job = subprocess.check_output(args, text=True).strip().split(';')[0]
    assert job.isdigit()
    jobs.append(dict(partition=partition, job=job))
    (root / 'jobs.json').write_text(json.dumps(jobs, indent=2) + '\n')
    print(partition, job, flush=True)
