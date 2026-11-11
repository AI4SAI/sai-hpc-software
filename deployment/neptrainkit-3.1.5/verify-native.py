#!/usr/bin/env python3
"""Recheck frozen native outputs and completed Slurm accounting."""
import json
from pathlib import Path
import subprocess
import sys

import install
from prepare import PREFIX, digest

run = Path(sys.argv[1])
request = json.loads((run / 'request.json').read_text())
submitted = json.loads((run / 'submission.json').read_text())
assert digest(run / 'request.json') == submitted['request_sha256']
assert digest(run / 'job.sbatch') == submitted['script_sha256']
for name, expected in request['files'].items():
    assert digest(run / name) == expected, name
assert request['release_sha256'] == digest(install.PACKAGE / 'release.json')
prefix = Path(request['prefix'])
assert str(prefix).endswith(PREFIX)
root = Path(str(prefix).removesuffix(PREFIX) or '/')
install.check(install.metadata(), root, installed=True)
accounting = subprocess.check_output(['sacct','-X','-n','-P','-j',submitted['job'],
    '-o','JobIDRaw,State,ExitCode,Partition,Account,WorkDir,NodeList,Elapsed'],text=True)
row, = [line.split('|') for line in accounting.splitlines() if line.split('|')[0]==submitted['job']]
assert row[1:6] == ['COMPLETED','0:0','16V100','proj-sai-hpc-software',str(run)]
science = json.loads((run/'science.json').read_text())
gui = json.loads((run/'gui.json').read_text())
assert science['passed'] is True and gui['passed'] is True
assert science['implementation'].startswith(str(prefix) + '/')
assert science['gpu_mask'] not in (None,'','-1')
assert 'not found' not in (run/'loader.txt').read_text()
assert str(prefix) in (run/'loader.txt').read_text()
assert 'NO_LD_LIBRARY_PATH_IMPORT_PASSED' in (run/'no-ld-library-path.txt').read_text()
assert (run/'unload.passed').is_file()
report = dict(passed=True, route=request['route'], accounting=row,
    source=install.SOURCE, version='3.1.5', image_sha256=install.IMAGE_SHA256,
    runtime=request['runtime'], science=science, gui=gui,
    comparison_tolerances=dict(cpu_reference_atol=dict(energy=5e-4,virial=5e-4,forces=5e-6),
        cuda_vs_cpu=dict(atol=2e-6,rtol=1e-6)),
    files={name:digest(run/name) for name in ('request.json','submission.json','job.sbatch',
        'science.json','gui.json','loader.txt','modules.txt','no-ld-library-path.txt','unload.passed')})
print(json.dumps(report,indent=2,sort_keys=True))
