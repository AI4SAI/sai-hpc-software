#!/usr/bin/env python3
"""Actions transport and visible result for independent daily beta rotation."""
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import tarfile
import time

from beta_candidates import validate_plan
from release_contract import SOFTWARE
from remote_controller import SAI_PROJECT_ROOT, safe_name, safe_sha
from source_cache import checksum

REPO = Path(__file__).resolve().parents[1]


def add_bytes(archive, name, data):
    info = tarfile.TarInfo(name)
    info.size, info.mode = len(data), 0o444
    archive.addfile(info, io.BytesIO(data))


def package(path, builds):
    """Only checked-in trusted controllers and already-reviewed fixture bytes."""
    inputs = REPO / 'docs/evidence/sif-lmod-20261024/raw/4V100'
    files = {'abacus': ['INPUT', 'KPT', 'STRU', 'PP_ORB/As_dojo.upf', 'PP_ORB/Ga_dojo.upf'],
             'cp2k': ['input.inp'], 'lammps': ['in.lmp', 'data.lmp'],
             'gpumd': ['run.in', 'nep.txt', 'model.xyz', 'gold.xyz'],
             'deepmd-kit': ['fixture.json', 'model.pth']}
    old = json.loads((REPO / 'deployment/sif-development-20261024/check-plan.json').read_text())['4V100']
    references = {'abacus': dict(energy_ev=old['abacus_energy_ev']), 'cp2k': old['cp2k_reference'],
                  'lammps': old['lammps_reference'], 'gpumd': {}, 'deepmd-kit': {}}
    with tarfile.open(path, 'w:gz') as archive:
        for source in sorted((REPO / 'controller').iterdir()):
            if source.is_file() and source.suffix in ('.py', '.sh', '.json', '.f90', '.cmake'):
                archive.add(source, arcname='controller/' + source.name)
        for software, names in files.items():
            source_folder = inputs / ('deepmd' if software == 'deepmd-kit' else software)
            for name in names:
                archive.add(source_folder / name, arcname='fixtures/' + software + '/' + name)
        archive.add(REPO / 'deployment/sif-development-20261024/deepmd-check.py', arcname='fixtures/deepmd-kit/check.py')
        add_bytes(archive, 'fixtures/references.json', json.dumps(references, sort_keys=True).encode())
        add_bytes(archive, 'builds.json', json.dumps(builds, sort_keys=True).encode())


def main():
    software = os.environ['BETA_SOFTWARE']
    builds = json.loads(os.environ['BETA_BUILDS'])
    selected = [row for row in builds if row['track'] == 'development']
    if not selected or {row['target'] for row in selected} != set(SOFTWARE[software]['targets']):
        print('BETA_SKIPPED: no complete development partition matrix selected', flush=True)
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as out:
            out.write('Beta unchanged: this request did not select the complete development partition matrix.\n')
        return
    validate_plan(software, builds)
    workflow = os.environ.get('BETA_SOURCE_RUN') or os.environ['GITHUB_RUN_ID']
    attempt = os.environ.get('BETA_SOURCE_ATTEMPT') or os.environ['GITHUB_RUN_ATTEMPT']
    name = safe_name(workflow + '-' + attempt + '-' + software)
    commit = safe_sha(os.environ['GITHUB_SHA'])
    temporary = Path(os.environ['RUNNER_TEMP'])
    results = temporary / 'beta-results'
    results.mkdir(exist_ok=True)
    snapshot = str(SAI_PROJECT_ROOT / 'controller/beta' / commit / name)
    task = str(SAI_PROJECT_ROOT / 'operations/daily-beta' / (name + '-' + commit[:12]))
    user = safe_name(os.environ['REMOTE_USER'])
    remote = user + '@c0.sai.ai-4s.com'
    options = ['-i', str(temporary / 'ssh/key'), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'UserKnownHostsFile=' + str(REPO / '.ci/slurm/known_hosts'), '-o', 'ConnectTimeout=20',
               '-o', 'ServerAliveInterval=20', '-o', 'ServerAliveCountMax=6']

    def ssh(argv, **kw):
        return subprocess.run(['ssh', *options, '-p', '12022', remote, shlex.join(list(map(str, argv)))],
                              text=True, check=True, **kw)

    archive = temporary / 'beta-controller.tar.gz'
    package(archive, builds)
    ssh(['mkdir', '-p', snapshot])
    # Retry only the content transfer. Immutable controller comparison below
    # prevents a retry or another invocation from changing an active snapshot.
    for index in range(3):
        try:
            upload = subprocess.run(['scp', '-q', *options, '-P', '12022', str(archive), remote + ':' + snapshot + '/payload.tar'],
                                    timeout=1800, check=True)
            break
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if index == 2:
                raise
        time.sleep(5 * (index + 1))
    unpack = '''from pathlib import Path
import hashlib, sys, tarfile
root=Path(sys.argv[1]); archive=root/'payload.tar'
assert hashlib.sha256(archive.read_bytes()).hexdigest()==sys.argv[2]
with tarfile.open(archive) as tar:
 for member in tar.getmembers():
  assert member.isfile() and not Path(member.name).is_absolute() and '..' not in Path(member.name).parts
  path=root/member.name; data=tar.extractfile(member).read()
  assert path.resolve()==path
  if path.exists(): assert path.read_bytes()==data
  else:
   path.parent.mkdir(parents=True,exist_ok=True)
   with path.open('xb') as out: out.write(data)
   path.chmod(0o444)
'''
    ssh(['python3', '-c', unpack, snapshot, checksum(archive)])
    code = 1
    try:
        # The remote task is restartable; completed submissions and publication
        # receipts are reused after a lost SSH reply, without duplicate jobs.
        command = ['env', 'PYTHONDONTWRITEBYTECODE=1', 'python3', snapshot + '/controller/daily_beta.py',
                   '--software', software, '--workflow', workflow, '--attempt', attempt,
                   '--builds', snapshot + '/builds.json', '--fixtures', snapshot + '/fixtures', '--task', task]
        for index in range(3):
            try:
                ssh(command)
                code = 0
                break
            except subprocess.CalledProcessError as error:
                code = error.returncode
                if code != 255 or index == 2:
                    break
                time.sleep(5 * (index + 1))
    finally:
        for name in ('request.json', 'status.json', 'publication.json', 'jobs.json', 'runtime-status.json', 'validation.json', 'candidates.json'):
            subprocess.run(['scp', '-q', *options, '-P', '12022', remote + ':' + task + '/' + name, str(results / name)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
        # Preserve raw numerical outputs and logs, including failed first
        # allocations. The archive excludes bundles, images and build trees.
        collect = """from pathlib import Path
import sys, tarfile
root=Path(sys.argv[1])
with tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as tar:
 for directory in root.iterdir():
  if directory.name not in ('4V100','16V100','8V100V0','DSPRHBM','4V100-retry1','16V100-retry1','8V100V0-retry1','DSPRHBM-retry1'): continue
  for path in directory.rglob('*'):
   if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(directory):
    tar.add(path,arcname=str(path.relative_to(root)),recursive=False)
"""
        with (results / 'runtime-evidence.tar').open('wb') as stream:
            try:
                ssh(['python3', '-c', collect, task], stdout=stream, timeout=600)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                print('Raw evidence transport failed; retained on project storage: ' + str(error), flush=True)
        record = json.loads((results / 'status.json').read_text()) if (results / 'status.json').exists() else {'status': 'transport_failed'}
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as out:
            out.write(f'### {software} beta publication\n\n')
            out.write('```json\n' + json.dumps(record, indent=2) + '\n```\n')
    if code:
        raise RuntimeError('Beta rotation failed; previous public generation retained. See the publication summary and project task: ' + task)


if __name__ == '__main__':
    main()
