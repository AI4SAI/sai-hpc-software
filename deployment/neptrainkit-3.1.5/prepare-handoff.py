#!/usr/bin/env python3
"""Freeze the accepted SIF, standard runtime projection and static build record."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import install
from prepare import PREFIX, SOURCE, digest

run = Path(sys.argv[1])
package = Path('/home/stardust/sai-hpc-software/deployment/neptrainkit-3.1.5')
assert install.PACKAGE == package
proof = json.loads((run / 'results/acceptance.json').read_text())
accounting = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', proof['job'],
    '-o', 'JobIDRaw,State,ExitCode,Partition,Account,WorkDir'], text=True)
row, = [line.split('|') for line in accounting.splitlines() if line.split('|')[0] == proof['job']]
assert row[1:6] == ['COMPLETED', '0:0', '16V100', 'proj-sai-hpc-software', str(run)]
assert proof['passed'] is True and proof['source'] == SOURCE
request = json.loads((run / 'request.json').read_text())
assert digest(run / 'request.json') == proof['request_sha256']
origin = Path(request['origin'])
payload = origin / 'rootfs' / PREFIX.lstrip('/')
runtime = install.inventory(payload, projected=True)
image = Path(proof['image'])
assert digest(image) == proof['image_sha256'] == install.IMAGE_SHA256


def copy(source, destination):
    if destination.exists():
        assert digest(source) == digest(destination), destination
    else:
        shutil.copyfile(source, destination)
    destination.chmod(0o444)


def write_record(path, content):
    install.plain(path)
    if path.exists() and path.read_text() == content:
        return
    temporary = path.with_name('.' + path.name + '.prepared-' + str(os.getpid()))
    with temporary.open('x') as stream:
        stream.write(content)
    temporary.chmod(0o444)
    temporary.replace(path)


copy(image, package / 'neptrainkit-3.1.5.sif')
copy(run / 'results/acceptance.json', package / 'acceptance.json')
copy(origin / 'runtime/requirements.txt', package / 'requirements.txt')
copy(origin / 'runtime/packages.json', package / 'packages.json')
text = '\n'.join([
    'NepTrainKit 3.1.5; source ' + SOURCE,
    'Features: NEP dataset preparation/filtering/visualization; Qt 6.11.1 GUI;',
    'CPU/OpenMP and CUDA inference via private nep-adapters 1.0.5; Python 3.12.13.',
    'Installation: ' + PREFIX,
    'Executables: ' + PREFIX + '/bin/nepkit, ' + PREFIX + '/bin/NepTrainKit',
    'Python and packages: ' + PREFIX + '/bin/python; ' + PREFIX + '/lib/python3.12/site-packages',
    'Module: neptrainkit/3.1.5; one shared build for compatible x86-64 hosts, no MPI.',
    'Application native helpers: GCC 14.3.0; OpenMP; -march=x86-64 -mtune=generic.',
    'NEPAdapters CPU wheel: native architecture specialization OFF; no AVX2/AVX-512 requirement.',
    'Actual CPU extension disassembly: no YMM/ZMM registers or VEX/EVEX mnemonics.',
    'NumPy/OpenBLAS: upstream wheel runtime CPU dispatch; the full environment was tested on SAI.',
    'CUDA wheel: sm_60/sm_89 SASS and compute_60 PTX, CUDA 12.4 runtime; V100 SM70 tested.',
    'Qualification: job ' + proof['job'] + ', 16v100n02, Tesla V100-SXM2-16GB.',
    '45 upstream tests + 6 subtests passed; installed helper imports passed.',
    'CPU vs upstream reference absolute tolerances: energy/virial 5e-4, force 5e-6.',
    'CUDA vs CPU comparison: abs(actual-reference) <= 2e-6 + 1e-6 * abs(reference).',
    'GPU maximum errors: energy 1.7283e-5, force 2.1852e-6, virial 1.3531e-7, descriptor 3.7408e-7.',
    'GUI: offscreen main-window creation and clean shutdown passed; OpenGL drawing and remote display not qualified.',
    'Interactive use requires remote desktop/X11. The nepkit command is a GUI, not a batch training engine.',
    'Installed package versions: cat ' + str(package / 'requirements.txt'),
    'SIF SHA256: ' + install.IMAGE_SHA256,
    'Native payload files: ' + str(runtime['files']) + '; bytes: ' + str(runtime['bytes']),
    'Runtime sidecar updates installed by a user are outside this pinned application qualification.',
    ''])
write_record(package / 'build-info.txt', text)
release = dict(schema=1, version='3.1.5', source=SOURCE, prefix=PREFIX,
    image_sha256=install.IMAGE_SHA256, image_bytes=image.stat().st_size,
    runtime=runtime, accounting=row,
    records={name: digest(package / name) for name in ('acceptance.json', '3.1.5',
            'build-info.txt', 'requirements.txt', 'packages.json')})
write_record(package / 'release.json', json.dumps(release, indent=2, sort_keys=True) + '\n')
install.check(install.metadata(), Path('/'))
print(json.dumps(release, indent=2))
