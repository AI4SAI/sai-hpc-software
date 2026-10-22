"""Submit an isolated, read-only-old-image CP2K configure/link diagnostic."""
from pathlib import Path
import hashlib
import json
import shlex
import shutil
import subprocess
import sys

project = Path('/home/stardust/sai-hpc-software')
checkout = project / 'development/workflow-closure'
old = project / 'runs/39041714911-1-release-4v100-avx512-2026-10-17-67b5da876dd6'
run = project / 'runs/cp2k-cosma-preflight-20261018-b'
job = (old / 'job.id').read_text().strip()
state = subprocess.check_output(['sacct', '-X', '-nP', '-j', job, '-o', 'JobIDRaw,State,ExitCode'], text=True)
assert f'{job}|FAILED|1:0' in state, state
assert not run.exists(), run
run.mkdir()
for name in ('input', 'results', 'runtime', 'cache'):
    (run / name).mkdir()
control = run / 'input/controller'
shutil.copytree(checkout / 'controller', control, ignore=shutil.ignore_patterns('__pycache__'))
sys.path.insert(0, str(control))
from remote_controller import container_command
from cp2k_image_driver import wrap_command

receipt = json.loads((old / 'results/install-success.json').read_text())
prefix = receipt['install_prefix']
record = {n['name']: n for n in receipt['nodes']}
source_paths = {}
archive_receipts = {}
for name, version, digest in (
    ('costa', '2.3.3', 'e007ec640c988fb04f5659228f7cc7733bb3c4621cd1fa65f8671420b6807852'),
    ('cosma', '2.7.0', 'f4775d18379539d7bb5053bff8acb4e13d6ed31a9677f498d9099a7500488789'),
):
    archive = project / 'cache/spack/sources' / name / (name + '-' + version + '.tar.gz')
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == digest, archive
    destination = run / 'input' / (name + '-source')
    destination.mkdir()
    subprocess.run(['tar', '--no-same-owner', '--strip-components=1', '-xzf', str(archive), '-C', str(destination)], check=True)
    assert (destination / 'CMakeLists.txt').is_file()
    source_paths[name] = '/probe/input/' + name + '-source'
    archive_receipts[name] = dict(archive=str(archive), sha256=digest)

# Use the exact production argument construction, but write the configure
# result only into this diagnostic. The old image is never an output.
original = (control / 'cp2k_build.sh').read_text()
initial = original.split('tblite_root=', 1)[0].replace('/workspace/cp2k-spack-build-env.sh', '/probe/build-env.sh')
prefixes = original[original.index('prefixes=(\n'):original.index('# Explicit adapters')]
configure = original[original.index('cmake_args=(\n'):original.index('python3 /control/cp2k_feature_contract.py cache')]
configure = configure.replace('-B /workspace/build', '-B /probe/cp2k-build')
configure = configure.replace('cmake "${cmake_args[@]}"',
    'cmake "${cmake_args[@]}" -Dcosma_DIR=/probe/cosma/lib/cmake/cosma -Dcosta_DIR=/probe/costa/lib/cmake/costa')

build_libraries = r'''
export CUDA_CACHE_PATH=/probe/cache
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
scalapack="$OPENBLAS_ROOT/lib/libscalapack.so"
test -f "$scalapack"
common=(-DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON -DCMAKE_INSTALL_LIBDIR=lib
  -DCMAKE_CXX_COMPILER="$CXX" -DMPI_CXX_COMPILER="$MPI_HOME/bin/mpicxx"
  -DCMAKE_CXX_FLAGS="-O3 -march=native -mtune=native")
cmake -S "$costa_source" -B /probe/costa-build "${common[@]}" \
  -DCMAKE_INSTALL_PREFIX=/probe/costa -DCOSTA_SCALAPACK=CUSTOM \
  -DCOSTA_SCALAPACK_LINK_LIBRARIES:FILEPATH="$scalapack" \
  -DCOSTA_WITH_TESTS=OFF -DCOSTA_WITH_APPS=OFF -DCOSTA_WITH_BENCHMARKS=OFF -DCOSTA_WITH_PROFILING=OFF
cmake --build /probe/costa-build --parallel "$jobs"
cmake --install /probe/costa-build
cmake -S "$cosma_source" -B /probe/cosma-build "${common[@]}" \
  -DCMAKE_INSTALL_PREFIX=/probe/cosma -DCOSMA_SCALAPACK=CUSTOM \
  -DCOSMA_SCALAPACK_LINK_LIBRARIES:FILEPATH="$scalapack" -DCOSMA_BLAS=CUDA \
  -DCMAKE_CUDA_ARCHITECTURES=70 \
  -DCOSMA_WITH_TESTS=OFF -DCOSMA_WITH_APPS=OFF -DCOSMA_WITH_BENCHMARKS=OFF -DCOSMA_WITH_PROFILING=OFF \
  -DCOSMA_WITH_NCCL=OFF -DCOSMA_WITH_RCCL=OFF -DCOSMA_WITH_GPU_AWARE_MPI=OFF \
  -DCMAKE_DISABLE_FIND_PACKAGE_MKL=TRUE -DOPENBLAS_ROOT="$OPENBLAS_ROOT" \
  -DCOSMA_OPENBLAS_LINK_LIBRARIES:FILEPATH="$OPENBLAS_ROOT/lib/libopenblas.so" \
  -DCOSMA_OPENBLAS_INCLUDE_DIRS:PATH="$OPENBLAS_ROOT/include" \
  -Dcosta_DIR=/probe/costa/lib/cmake/costa \
  -DCMAKE_PREFIX_PATH="/probe/costa;$tiled_mm;$OPENBLAS_ROOT;$MPI_HOME;/opt/devtools/nvidia/cuda-12.9.1"
cmake --build /probe/cosma-build --parallel "$jobs"
cmake --install /probe/cosma-build
test -f /probe/cosma/lib/libcosma_pxgemm_cpp.so
test -f /probe/cosma/lib/libcosma_prefixed_pxgemm.so
echo CP2K_COSMA_SCALAPACK_LIBRARIES_BUILT
'''
shell = '\n'.join([
    'set -euo pipefail', 'umask 022',
    'export INSTALL_PREFIX=' + shlex.quote(prefix),
    'export CP2K_SOURCE_SHA=67b5da876dd6a76b8b021d5a04d1c81ba79a4c50',
    'source /control/environment.sh 4v100-avx512',
    initial,
    'costa_source=' + shlex.quote(source_paths['costa']),
    'cosma_source=' + shlex.quote(source_paths['cosma']),
    'tiled_mm=' + shlex.quote(record['tiled-mm']['prefix']),
    build_libraries,
    'tblite_root="$prefix/dependencies/tblite"',
    prefixes,
    'prefixes+=("$prefix/dependencies/libxs" /probe/costa /probe/cosma)',
    'cmake_prefix=$(IFS=";"; echo "${prefixes[*]}")',
    configure,
    'echo CP2K_COMPLETE_CONFIGURE_PASSED',
])
(run / 'input/probe.sh').write_text(shell + '\n')
subprocess.run(['bash', '-n', str(run / 'input/probe.sh')], check=True)
command = container_command(project / 'containers/base/minimal-v1.sif',
    ['/usr/bin/env', 'SAI_BUILD_PARTITION=4V100', '/bin/bash', '/probe/input/probe.sh', '4v100-avx512'],
    overlay=str(old / 'work.ext3') + ':ro', control=control, jobs=8, gpu=True,
    extra_binds=[(old / 'results', '/results'), ('/etc/alternatives', '/etc/alternatives'),
                 ('/usr/bin', '/bin')], runtime=run / 'runtime')
command[2:2] = ['--bind', str(run) + ':/probe:rw']
command = wrap_command(project, run, control, command)
q = shlex.quote
lines = ['#!/bin/bash', '#SBATCH --job-name=cp2k-cosma-full-config', '#SBATCH --partition=4V100',
    '#SBATCH --qos=flood-1o2gpu', '#SBATCH --nodes=1', '#SBATCH --ntasks=1',
    '#SBATCH --gpus-per-node=1', '#SBATCH --nodelist=4v100n02', '#SBATCH --time=40', '#SBATCH --export=NIL',
    '#SBATCH --output=' + str(run / 'results/slurm-%j.log'),
    'set -euo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""',
    'export TMPDIR=' + str(run / 'runtime'), 'export APPTAINER_TMPDIR="$TMPDIR"',
    'export APPTAINER_CACHEDIR=' + str(run / 'cache'),
    'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
    'cd ' + q(str(run)), 'sha256sum --check --status input.sha256',
    'sha256sum ' + q(str(old / 'work.ext3')) + ' > results/image-before.sha256',
    'set +e', shlex.join(command), 'status=$?', 'set -e',
    'printf "%s\\n" "$status" > results/config.exit',
    'sha256sum ' + q(str(old / 'work.ext3')) + ' > results/image-after.sha256',
    'cmp results/image-before.sha256 results/image-after.sha256',
    'echo READONLY_APPLICATION_IMAGE_UNCHANGED', 'exit "$status"']
script = run / 'input/job.sbatch'
script.write_text('\n'.join(lines) + '\n')
subprocess.run(['bash', '-n', str(script)], check=True)
(run / 'input.sha256').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + str(p.relative_to(run)) + '\n'
    for p in sorted((run / 'input').rglob('*')) if p.is_file()))
result = subprocess.check_output(['sbatch', '--parsable', str(script)], text=True).strip().split(';')[0]
assert result.isdigit(), result
(run / 'job.id').write_text(result + '\n')
(run / 'request.json').write_text(json.dumps(dict(
    purpose='isolated-cosma-scalapack-full-cp2k-configure-not-artifact',
    old_application_run=str(old), old_application_job=job, job_id=result,
    controller_commit=subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True).strip(),
    original_build_script_sha256=hashlib.sha256(original.encode()).hexdigest(),
    source_paths=source_paths, source_archives=archive_receipts), indent=2) + '\n')
print(json.dumps(dict(run=str(run), job_id=result)), flush=True)
