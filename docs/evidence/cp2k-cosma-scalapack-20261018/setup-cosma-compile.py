from pathlib import Path
import hashlib
import json
import shlex
import shutil
import subprocess
import sys

project = Path('/home/stardust/sai-hpc-software')
probe = project / 'runs/cp2k-cosma-preflight-20261018-b'
run = project / 'runs/cp2k-cosma-compile-20261018-a'
old = project / 'runs/39041714911-1-release-4v100-avx512-2026-10-17-67b5da876dd6'
assert (probe / 'results/config.exit').read_text().strip() == '0'
assert (probe / 'results/image-before.sha256').read_bytes() == (probe / 'results/image-after.sha256').read_bytes()
state = subprocess.check_output(['sacct', '-X', '-nP', '-j', '1833202', '-o', 'JobIDRaw,State,ExitCode'], text=True)
assert '1833202|COMPLETED|0:0' in state, state
run.mkdir()
for name in ('input', 'runtime', 'results', 'cache'):
    (run / name).mkdir()
control = run / 'input/controller'
shutil.copytree(probe / 'input/controller', control, ignore=shutil.ignore_patterns('__pycache__'))
sys.path.insert(0, str(control))
from remote_controller import container_command
from cp2k_image_driver import wrap_command

shell = r'''set -euo pipefail
umask 022
source /control/environment.sh 4v100-avx512
module unload gcc
module load gcc/14.3.0 spglib/2.7.0
export CC=/opt/devtools/gcc/14.3.0/bin/gcc CXX=/opt/devtools/gcc/14.3.0/bin/g++ FC=/opt/devtools/gcc/14.3.0/bin/gfortran
export OMPI_CC="$CC" OMPI_CXX="$CXX" OMPI_FC="$FC"
export CUDA_CACHE_PATH=/runtime PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
source /probe/build-env.sh
export LD_LIBRARY_PATH="/probe/cosma/lib:/probe/costa/lib:$cp2k_spack_runtime_ld:$LD_LIBRARY_PATH"
cmake --build /probe/cp2k-build --parallel 8
echo CP2K_DIAGNOSTIC_COMPILE_PASSED_NOT_ARTIFACT
python3 - <<'PY'
from pathlib import Path
import json,subprocess
binaries=[p for p in Path('/probe/cp2k-build').rglob('cp2k.psmp') if p.is_file()]
assert len(binaries)==1,binaries
exe=binaries[0]
version=subprocess.run([str(exe),'--version'],capture_output=True,text=True,check=True)
Path('/compile-results/version.txt').write_text(version.stdout+version.stderr)
linked=subprocess.run(['ldd',str(exe)],capture_output=True,text=True,check=True)
Path('/compile-results/ldd.txt').write_text(linked.stdout+linked.stderr)
assert 'not found' not in linked.stdout+linked.stderr
print(version.stdout)
print('CP2K_DIAGNOSTIC_VERSION_LDD_PASSED_NOT_SIF')
PY
'''
(run / 'input/compile.sh').write_text(shell)
subprocess.run(['bash', '-n', str(run / 'input/compile.sh')], check=True)
command = container_command(project / 'containers/base/minimal-v1.sif',
    ['/bin/bash', '/compile-input/compile.sh'], overlay=str(old / 'work.ext3') + ':ro',
    control=control, jobs=8, gpu=True, runtime=run / 'runtime',
    extra_binds=[(run / 'input', '/compile-input'), ('/etc/alternatives', '/etc/alternatives'), ('/usr/bin', '/bin')])
command[2:2] = ['--bind', str(probe) + ':/probe:rw', '--bind', str(run / 'results') + ':/compile-results:rw']
command = wrap_command(project, run, control, command)
q = shlex.quote
lines = ['#!/bin/bash', '#SBATCH --job-name=cp2k-compile-ahead', '#SBATCH --partition=4V100',
    '#SBATCH --qos=flood-1o2gpu', '#SBATCH --nodes=1', '#SBATCH --ntasks=1',
    '#SBATCH --gpus-per-node=1', '#SBATCH --nodelist=4v100n02', '#SBATCH --time=90', '#SBATCH --export=NIL',
    '#SBATCH --output=' + str(run / 'results/slurm-%j.log'),
    'set -euo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""',
    'export TMPDIR=' + str(run / 'runtime'), 'export APPTAINER_TMPDIR="$TMPDIR"',
    'export APPTAINER_CACHEDIR=' + str(run / 'cache'),
    'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
    'cd ' + q(str(run)), 'sha256sum --check --status input.sha256',
    'sha256sum ' + q(str(old / 'work.ext3')) + ' > results/image-before.sha256',
    'cmp results/image-before.sha256 ' + q(str(probe / 'results/image-after.sha256')),
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
(run / 'request.json').write_text(json.dumps(dict(purpose='cp2k-full-compile-diagnostic-not-sif',
    job_id=job, configuration_job='1833202', configuration_run=str(probe),
    old_application_run=str(old), source_sha='67b5da876dd6a76b8b021d5a04d1c81ba79a4c50'), indent=2) + '\n')
print(json.dumps(dict(run=str(run), job_id=job)), flush=True)
