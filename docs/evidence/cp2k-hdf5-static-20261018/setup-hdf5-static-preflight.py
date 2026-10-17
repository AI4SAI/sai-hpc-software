"""Relink completed CP2K objects using HDF5's native static dependency targets."""
from pathlib import Path
import hashlib
import json
import shlex
import shutil
import subprocess
import sys

project = Path('/home/stardust/sai-hpc-software')
staging = project / 'diagnostics/cp2k-release-20261016/hdf5-static-fix'
previous = project / 'runs/cp2k-cosma-preflight-20261018-b'
old = project / 'runs/39041714911-1-release-4v100-avx512-2026-10-17-67b5da876dd6'
candidate = project / 'runs/cp2k-tblite-release-preflight-20261018-c'
run = project / 'runs/cp2k-hdf5-static-preflight-20261018-b'
assert (candidate / 'results/compile.exit').read_text().strip() == '2'
state = subprocess.check_output(['sacct', '-X', '-nP', '-j', '1833642', '-o', 'JobIDRaw,State,ExitCode'], text=True)
assert '1833642|FAILED|2:0' in state, state
assert (candidate / 'results/image-before.sha256').read_bytes() == (candidate / 'results/image-after.sha256').read_bytes()
assert (candidate / 'cp2k-build/src/CMakeFiles/cp2k.dir/tblite_interface.F.o').is_file()
prior = project / 'runs/cp2k-hdf5-static-preflight-20261018-a'
assert (prior / 'results/compile.exit').read_text().strip() == '1'
assert (prior / 'results/image-before.sha256').read_bytes() == (prior / 'results/image-after.sha256').read_bytes()
state = subprocess.check_output(['sacct', '-X', '-nP', '-j', '1833954', '-o', 'JobIDRaw,State,ExitCode'], text=True)
assert '1833954|FAILED|1:0' in state, state
run.mkdir()
for name in ('input', 'runtime', 'results', 'cache', 'results/before'):
    (run / name).mkdir()
shutil.copyfile(candidate / 'results/image-after.sha256', run / 'results/image-before.sha256')
for source, destination in (
    ('cp2k-build/CMakeCache.txt', 'CMakeCache.txt'),
    ('cp2k-build/src/CMakeFiles/cp2k.dir/link.txt', 'cp2k-link.txt'),
    ('results/slurm-1833642.log', 'slurm-1833642.log'),
):
    shutil.copyfile(candidate / source, run / 'results/before' / destination)
control = run / 'input/controller'
shutil.copytree(candidate / 'input/controller', control, ignore=shutil.ignore_patterns('__pycache__'))
for name in ('cp2k_build.sh', 'cp2k_cmake_prerequisites.cmake'):
    shutil.copyfile(staging / name, control / name)
sys.path.insert(0, str(control))
from remote_controller import container_command
from cp2k_image_driver import wrap_command

prefix = json.loads((old / 'results/install-success.json').read_text())['install_prefix']
original = (control / 'cp2k_build.sh').read_text()
initial = original.split('tblite_root=', 1)[0].replace('/workspace/cp2k-spack-build-env.sh', '/verification/build-env.sh')
prefixes = original[original.index('prefixes=(\n'):original.index('# Explicit adapters')]
configure = original[original.index('cmake_args=(\n'):original.index('python3 /control/cp2k_feature_contract.py cache')]
configure = configure.replace('-B /workspace/build', '-B /candidate/cp2k-build')
configure = configure.replace('cmake "${cmake_args[@]}"',
    'cmake "${cmake_args[@]}" -DHDF5_FIND_DEBUG=ON -Dcosma_DIR=/probe/cosma/lib/cmake/cosma -Dcosta_DIR=/probe/costa/lib/cmake/costa')
verify = r'''
python3 - <<'PY'
from pathlib import Path
import json
import os
import shlex
import shutil
import sys
sys.path.insert(0, '/control')
from cp2k_feature_contract import check_cache
build=Path('/candidate/cp2k-build')
cache=check_cache((build/'CMakeCache.txt').read_text(), os.environ['INSTALL_PREFIX'], '4v100-avx512')
assert cache['HDF5_USE_STATIC_LIBRARIES'] in ('ON','TRUE','1')
link=shlex.split((build/'src/CMakeFiles/cp2k.dir/link.txt').read_text())
libraries=[word for word in link if 'hdf5' in word.lower()]
assert any(word.endswith('/libhdf5_f90cstub.a') for word in libraries), libraries
Path('/verification/results/hdf5-link-libraries.json').write_text(json.dumps(libraries,indent=2)+'\n')
shutil.copyfile(build/'CMakeCache.txt','/verification/results/CMakeCache.txt')
print('HDF5_STATIC_STUB_LINK_VERIFIED '+json.dumps(libraries))
PY
export LD_LIBRARY_PATH="/probe/cosma/lib:/probe/costa/lib:$cp2k_spack_runtime_ld:$LD_LIBRARY_PATH"
cmake --build /candidate/cp2k-build --parallel "$jobs"
echo CP2K_RELEASE_FULL_COMPILE_PASSED_NOT_SIF
python3 - <<'PY'
from pathlib import Path
import hashlib
import json
import subprocess
import sys
sys.path.insert(0, '/control')
from cp2k_feature_contract import check_flags
exe=Path('/candidate/cp2k-build/bin/cp2k.psmp')
assert exe.is_file(),exe
version=subprocess.run([str(exe),'--version'],capture_output=True,text=True,check=True,timeout=120)
version_text=version.stdout+version.stderr
Path('/verification/results/version.txt').write_text(version_text)
flags=check_flags(version_text,'4v100-avx512')
linked=subprocess.run(['ldd',str(exe)],capture_output=True,text=True,check=True,timeout=120)
linkage=linked.stdout+linked.stderr
Path('/verification/results/ldd.txt').write_text(linkage)
assert 'not found' not in linkage,linkage
Path('/verification/results/diagnostic-validation.json').write_text(json.dumps(dict(
    status='diagnostic-compile-version-ldd-flags-passed-not-sif',
    binary=str(exe),sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),flags=flags),indent=2)+'\n')
print(version_text)
print('CP2K_RELEASE_DIAGNOSTIC_VERSION_LDD_FLAGS_PASSED_NOT_SIF')
PY
'''
shell = '\n'.join([
    'set -euo pipefail', 'umask 022',
    'export INSTALL_PREFIX=' + shlex.quote(prefix),
    'export CP2K_SOURCE_SHA=67b5da876dd6a76b8b021d5a04d1c81ba79a4c50',
    'source /control/environment.sh 4v100-avx512', initial,
    'export CUDA_CACHE_PATH=/verification/cache PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 OMP_NUM_THREADS=1',
    'tblite_root=/candidate/tblite', prefixes,
    'prefixes+=("$prefix/dependencies/libxs" /probe/costa /probe/cosma)',
    'cmake_prefix=$(IFS=";"; echo "${prefixes[*]}")', configure,
    'echo CP2K_RELEASE_STATIC_HDF5_CONFIGURE_PASSED', verify])
(run / 'input/probe.sh').write_text(shell + '\n')
subprocess.run(['bash', '-n', str(run / 'input/probe.sh')], check=True)
command = container_command(project / 'containers/base/minimal-v1.sif',
    ['/usr/bin/env', 'SAI_BUILD_PARTITION=4V100', '/bin/bash', '/verification/input/probe.sh', '4v100-avx512'],
    overlay=str(old / 'work.ext3') + ':ro', control=control, jobs=8, gpu=True,
    repository=project / 'cache/repositories/cp2k', runtime=run / 'runtime',
    extra_binds=[(old / 'results', '/results'), (previous, '/probe'),
                 (project / 'cache/cp2k-probe', '/input/probe'),
                 ('/etc/alternatives', '/etc/alternatives'), ('/usr/bin', '/bin')])
command[2:2] = ['--bind', str(candidate) + ':/candidate:rw', '--bind', str(run) + ':/verification:rw']
command = wrap_command(project, run, control, command)
q = shlex.quote
lines = ['#!/bin/bash', '#SBATCH --job-name=cp2k-release-hdf5-link', '#SBATCH --partition=4V100',
    '#SBATCH --qos=flood-1o2gpu', '#SBATCH --nodes=1', '#SBATCH --ntasks=1',
    '#SBATCH --gpus-per-node=1', '#SBATCH --time=60', '#SBATCH --export=NIL',
    '#SBATCH --output=' + str(run / 'results/slurm-%j.log'),
    'set -euo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""',
    'export TMPDIR=' + str(run / 'runtime'), 'export APPTAINER_TMPDIR="$TMPDIR"',
    'export APPTAINER_CACHEDIR=' + str(run / 'cache'),
    'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
    'cd ' + q(str(run)), 'sha256sum --check --status input.sha256',
    'cmp results/image-before.sha256 ' + q(str(candidate / 'results/image-after.sha256')),
    'set +e', shlex.join(command), 'status=$?', 'set -e',
    'printf "%s\\n" "$status" > results/compile.exit',
    'sha256sum ' + q(str(old / 'work.ext3')) + ' > results/image-after.sha256',
    'cmp results/image-before.sha256 results/image-after.sha256',
    'echo READONLY_APPLICATION_IMAGE_UNCHANGED', 'exit "$status"']
script = run / 'input/job.sbatch'
script.write_text('\n'.join(lines) + '\n')
subprocess.run(['bash', '-n', str(script)], check=True)
(run / 'input.sha256').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + str(p.relative_to(run)) + '\n'
    for p in sorted((run / 'input').rglob('*')) if p.is_file()))
job = subprocess.check_output(['sbatch', '--parsable', str(script)], text=True).strip().split(';')[0]
assert job.isdigit(), job
(run / 'job.id').write_text(job + '\n')
(run / 'request.json').write_text(json.dumps(dict(purpose='cp2k-release-static-hdf5-relink-diagnostic-not-sif',
    job_id=job, expected_image_hash_from=str(candidate / 'results/image-after.sha256'),
    reused_object_run=str(candidate), configuration_run=str(previous),
    old_application_run=str(old), source_sha='67b5da876dd6a76b8b021d5a04d1c81ba79a4c50',
    build_script_sha256=hashlib.sha256(original.encode()).hexdigest()), indent=2) + '\n')
print(json.dumps(dict(run=str(run), job_id=job)), flush=True)
