#!/usr/bin/env python3
"""Freeze an exact clean checkout and launch the three release acceptance chains."""
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys

project = Path('/home/stardust/sai-hpc-software')
root = project / 'experimental/deepmd-lammps'
checkout = project / 'development/lammps-release-20261020'
diag = project / 'diagnostics/lammps-release-20261020'
commit = subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True).strip()
assert len(sys.argv) == 2 and re.fullmatch(r'[a-f0-9]{40}', sys.argv[1])
assert commit == sys.argv[1]
short = commit[:7]
assert not subprocess.check_output(['git', '-C', str(checkout), 'status', '--porcelain'], text=True).strip()
sys.dont_write_bytecode = True
sys.path.insert(0, str(checkout / 'controller'))
from md_tracking import fingerprint
recipe = fingerprint(checkout / 'controller')
control = root / 'controller/acceptance' / commit / 'lammps-release-20261020'
control.mkdir(parents=True)
files = [p for p in (checkout / 'controller').glob('md_*.*') if p.suffix in ('.py', '.sh', '.json')]
files += [checkout / 'controller' / name for name in (
    'remote_controller.py', 'source_cache.py', 'resolve_source.py', 'create_rootfs.sh',
    'release_contract.py', 'native_module.py', 'export_native.py', 'module_publication.py',
    'delivery_layout.py', 'cp2k_image_driver.py')]
for path in files:
    destination = control / path.name
    shutil.copyfile(path, destination)
    destination.chmod(0o444)
control.chmod(0o555)
assert fingerprint(control) == recipe
helper = diag / ('resume_acceptance-' + short + '.py')
assert not helper.exists()
shutil.copyfile(checkout / 'docs/evidence/lammps-release-20261020/resume_acceptance.py', helper)
helper.chmod(0o444)
record = dict(commit=commit, acceptance_controller=str(control), acceptance_recipe_sha256=recipe,
              helper=str(helper), helper_sha256=hashlib.sha256(helper.read_bytes()).hexdigest(), followers={})
build8 = root / 'runs/md-39427644750-1-2026-10-20-1dfd6f53c113abda'
with (build8 / 'results/follow-build.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
follower = diag / ('follow_8v_build-' + short + '.py')
assert not follower.exists()
shutil.copyfile(checkout / 'docs/evidence/lammps-release-20261020/follow_8v_build.py', follower)
follower.chmod(0o444)
for target in ('16V100', '4V100'):
    log = diag / (target.lower() + '-acceptance-' + short + '.log')
    with log.open('x') as output:
        child = subprocess.Popen(['/usr/bin/python3', '-u', str(helper), target, str(control), recipe],
                                 stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                 start_new_session=True, cwd=str(diag))
    record['followers'][target] = dict(pid=child.pid, log=str(log))
log = diag / ('8v-follow-build-' + short + '.log')
with log.open('x') as output:
    child = subprocess.Popen(['/usr/bin/python3', '-u', str(follower), str(control), recipe, str(helper)],
                             stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                             start_new_session=True, cwd=str(diag))
record['followers']['8V100V0'] = dict(pid=child.pid, log=str(log), follower=str(follower),
                                    follower_sha256=hashlib.sha256(follower.read_bytes()).hexdigest())
with (diag / ('acceptance-restart-' + short + '.json')).open('x') as output:
    json.dump(record, output, sort_keys=True)
    output.write('\n')
print(json.dumps(record, indent=2))
