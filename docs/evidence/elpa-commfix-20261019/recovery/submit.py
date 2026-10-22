#!/usr/bin/env python3
"""Incident recovery: preserve the cancelled run and continue its exact clone."""
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

root = Path('/home/stardust/sai-hpc-software')
old = root/'runs/39262701906-1-release-8v100v0-avx512-2026-10-19-67b5da876dd6-dependencies'
run = root/'runs/cp2k-deps-8v100v0-recovery-20261019'
previous = json.loads((old/'request.json').read_text())
controller = Path(previous['controller'])
sys.path.insert(0, str(controller))
from cp2k_dependency_stage import render, PURPOSE
from software_controller import recipe_fingerprint
from source_cache import checksum

assert previous['identity']['recipe_sha256'] == recipe_fingerprint('cp2k', controller)
assert previous['job_id'] not in subprocess.check_output(['squeue', '-h', '-u', 'stardust', '-o', '%i'], text=True).split()
state = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', previous['job_id'], '--format=State,ExitCode'], text=True)
assert state.startswith('CANCELLED'), state
assert not run.exists()
run.mkdir(mode=0o700)
for name in ('results', 'runtime', 'apptainer-cache', 'recovery'):
    (run/name).mkdir(mode=0o700)
print('Cloning interrupted dependency state into '+str(run), flush=True)
digest = checksum(old/'work.ext3')
subprocess.run(['cp', '--sparse=always', '--reflink=auto', str(old/'work.ext3'), str(run/'work.ext3')], check=True)
assert checksum(run/'work.ext3') == digest
for name in ('spack.yaml', 'spack.lock', 'libint-export-repair.json'):
    shutil.copyfile(old/'results'/name, run/'results'/name)
shutil.copyfile(Path(__file__).with_name('continue.sh'), run/'recovery/continue.sh')
shutil.copyfile(__file__, run/'recovery/submit.py')
shutil.copyfile(old/'request.json', run/'recovery/cancelled-request.json')
shutil.copyfile(old/'job.sbatch', run/'recovery/cancelled-job.sbatch')
identity = previous['identity']
text = render(root, controller, run, identity, minutes=240, jobs=12, overlay_mb=16384)
lines = text.splitlines()
start = next(i for i, line in enumerate(lines) if line.startswith('test ! -e '))
end = next(i for i, line in enumerate(lines) if line.startswith('/usr/sbin/e2fsck '))
lines[start:end] = [shlex.join(['printf', '%s  %s\n', digest, str(run/'work.ext3')])+' | sha256sum --check --status']
index = next(i for i, line in enumerate(lines) if 'cp2k_image_driver.py' in line)
command = shlex.split(lines[index])
position = command.index('exec') + 1
command[position:position] = ['--bind', str(run/'recovery')+':/recovery:ro']
image = command.index(str(root/'containers/base/minimal-v1.sif'))
command[image+1:] = ['/bin/bash', '/recovery/continue.sh', json.dumps(identity, sort_keys=True), checksum(old/'results/spack.lock')]
lines[index] = shlex.join(command)
script = run/'job.sbatch'
script.write_text('\n'.join(lines)+'\n')
script.chmod(0o700)
subprocess.run(['bash', '-n', str(script)], check=True)
request = dict(purpose=PURPOSE, identity=identity, controller=str(controller), script_sha256=checksum(script),
    minutes=240, overlay_mb=16384, recovery=dict(cancelled_run=str(old), cancelled_job_id=previous['job_id'],
    checkpoint_sha256=digest, continuation_sha256=checksum(run/'recovery/continue.sh'),
    reason='Agent validation step requested four CPUs per GPU; site policy requires six and cancelled the parent allocation.'))
job = subprocess.check_output(['sbatch', '--hold', '--parsable', str(script)], text=True).strip().split(';')[0]
assert job.isdigit()
request['job_id'] = job
(run/'job.id').write_text(job+'\n')
(run/'request.json').write_text(json.dumps(request, indent=2)+'\n')
subprocess.run(['scontrol', 'release', job], check=True)
print(json.dumps(request, indent=2), flush=True)
