#!/usr/bin/env python3
"""Internal check: keep MPI/private RAM alive through a same-user job epilog."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

output, bundle, package = map(Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location('accepted_runtime', bundle / 'libexec/sif_runtime.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
scope = runtime.memory_scope(os.environ)
assert scope and scope['job'] == os.environ['SLURM_JOB_ID']
status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
assert int(status['CapEff'].strip(), 16) == 0
job = os.environ['SLURM_JOB_ID']
sentinels = [runtime.memory_root(os.environ) / 'survivor', Path('/dev/shm') / ('survivor-' + job),
             Path('/tmp') / ('survivor-' + job)]
for marker in sentinels:
    marker.write_text(job)


def calculate(name):
    directory = output / name
    directory.mkdir()
    shutil.copyfile(output / 'input.inp', directory / 'input.inp')
    with (directory / 'stdout.txt').open('w') as stream:
        start = time.monotonic()
        subprocess.run(['cp2k.psmp', '-i', 'input.inp'], cwd=directory, stdout=stream,
                       stderr=subprocess.STDOUT, check=True)
        (directory / 'elapsed.txt').write_text(str(time.monotonic() - start))


calculate('before')
report = dict(job=job, node=os.uname().nodename, uid=os.getuid(), memory_scope=scope,
              sentinels=[str(p) for p in sentinels], catalog_sha256=runtime.digest(bundle / 'catalog.json'),
              runtime_sha256=runtime.digest(bundle / 'libexec/sif_runtime.py'))
(output / 'ready.json').write_text(json.dumps(report, indent=2) + '\n')
deadline = time.monotonic() + 600
while not (output / 'peer-completed.json').exists():
    if time.monotonic() > deadline:
        raise TimeoutError('peer job did not complete while the survivor was running')
    time.sleep(1)
assert all(marker.read_text() == job for marker in sentinels)
assert runtime.memory_scope(os.environ) == scope
calculate('after')
sys.path.insert(0, str(package.parents[1] / 'controller'))
from cp2k_benchmark import parse_science, compare_science
reference = json.loads((package / 'check-plan.json').read_text())[os.environ['SLURM_JOB_PARTITION']]['cp2k_reference']
for name in ('before', 'after'):
    directory = output / name
    sample = parse_science((directory / 'stdout.txt').read_text(), (directory / 'forces.xyz').read_text(),
                           (directory / 'elapsed.txt').read_text(), 'water-gpw')
    report[name] = compare_science(reference, sample)
report.update(passed=True, sentinels_survived=True,
              peer=json.loads((output / 'peer-completed.json').read_text()))
(output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
print('Private memory and CP2K survived the peer job epilog', flush=True)
