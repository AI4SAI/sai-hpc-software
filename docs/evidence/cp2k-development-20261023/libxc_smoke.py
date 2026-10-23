#!/usr/bin/env python3
"""Supplementary LibXC check of the unchanged CP2K development SIF.

Prepare runs only after the normal build has sealed its artifact. This script
does not change an artifact, its acceptance receipt, or any frozen controller.
"""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time

ROOT = Path('/org/org-open-sai/proj-sai-hpc-software')
SOURCE_SHA = 'cfe5e5d87aaf302953f14eb43587bef2411a8fb2'
LIBXC_SHA = '3915fac94416e4c415534223ea492ad2663f928acf27e98662c861b094a6c306'
NSYS = '/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/profilers/12.9/Nsight_Systems_2025.3/bin/nsys'
TOLERANCES = {
    'builtin_host': {'energy_ha': 1e-6, 'force_ha_bohr': 1e-5},
    'host_device': {'energy_ha': 1e-8, 'force_ha_bohr': 1e-7},
}
LIBXC_KERNEL = re.compile(r'work_gga_gpu_(?:exc|vxc)_unpol')

CHILD = '''#!/usr/bin/env bash
set +e
env LD_DEBUG=libs LD_DEBUG_OUTPUT=/work/loader "$@"
result=$?
printf '%s\\n' "$result" > /work/cp2k.exit
exit "$result"
'''

# This script runs inside the final SIF, after its original runtime-env.sh.
# All profiler and loader output goes to the supplementary case directory.
INNER = r'''#!/usr/bin/env bash
set -euo pipefail
driver=${LD_LIBRARY_PATH:-}
source "$1"
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH}${driver:+:$driver}"
export HOME=/runtime
prefix=${2%/bin/cp2k.psmp}
if [[ "$SAI_LIBXC_MODE" == inspect ]]; then
  "$2" --version > /work/version.txt
  ldd "$2" > /work/ldd.txt
  cp "$prefix/share/sai/CMakeCache.txt" /work/CMakeCache.txt
  cp "$prefix/share/sai/libxc-source-archives.sha256" /work/libxc-source-archives.sha256
  shopt -s nullglob
  libraries=("$prefix"/dependencies/libxc/lib/libxc*.so*)
  : > /work/libxc-libraries.sha256
  if (( ${#libraries[@]} )); then
    sha256sum "${libraries[@]}" > /work/libxc-libraries.sha256
  fi
elif [[ "$SAI_LIBXC_MODE" == device ]]; then
  "@NSYS@" --version > /work/nsys-version.txt
  "@NSYS@" profile --trace=cuda --sample=none --cpuctxsw=none \
    --output=/work/libxc-device /usr/bin/bash /work/cp2k-exec.sh "$2" "${@:3}"
  "@NSYS@" export --type=sqlite --output=/work/libxc-device.sqlite /work/libxc-device.nsys-rep
else
  exec /usr/bin/bash /work/cp2k-exec.sh "$2" "${@:3}"
fi
'''.replace('@NSYS@', NSYS)


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def write_json(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write('\n')


def controller_modules(control):
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(control))
    return tuple(importlib.import_module(name) for name in (
        'cp2k_benchmark', 'delivery_layout', 'remote_controller'))


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('frozen runtime launcher differs from reviewed template')
    return text.replace(before, after)


def instrument_launcher(control):
    text = (control / 'cp2k_runtime.sh').read_text()
    text = replace_once(text,
        'launcher_control=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)',
        'launcher_control=' + shlex.quote(str(control)))
    text = replace_once(text,
        '       --env "OMP_NUM_THREADS=${OMP_NUM_THREADS:-1}")',
        '       --env "OMP_NUM_THREADS=${OMP_NUM_THREADS:-1}"\n'
        '       --bind "$rank_runtime:/tmp:rw" --env HOME=/runtime\n'
        '       --env "SAI_LIBXC_MODE=${SAI_LIBXC_MODE:?}")')
    start = text.index('"${args[@]}" "$image" /usr/bin/bash --noprofile --norc -c \\\n')
    expected = '''"${args[@]}" "$image" /usr/bin/bash --noprofile --norc -c \\
  'driver=${LD_LIBRARY_PATH:-}; source "$1"; export LD_LIBRARY_PATH="${LD_LIBRARY_PATH}${driver:+:$driver}"; exec "$2" "${@:3}"' \\
  bash "$prefix/share/sai/runtime-env.sh" "$prefix/bin/cp2k.psmp" "$@"
'''
    if text[start:] != expected:
        raise ValueError('unexpected original final container command')
    return text[:start] + '''"${args[@]}" "$image" /usr/bin/bash --noprofile --norc /work/container-exec.sh \\
  "$prefix/share/sai/runtime-env.sh" "$prefix/bin/cp2k.psmp" "$@"
'''


def libxc_input(original, backend):
    return replace_once(original,
        '      &XC_FUNCTIONAL PBE\n      &END XC_FUNCTIONAL\n',
        '      LIBXC_GPU_BACKEND ' + backend + '\n'
        '      &XC_FUNCTIONAL\n'
        '        &GGA_X_PBE\n        &END GGA_X_PBE\n'
        '        &GGA_C_PBE\n        &END GGA_C_PBE\n'
        '      &END XC_FUNCTIONAL\n')


def artifact(request, delivery):
    manifest = delivery.load_artifact(ROOT, Path(request['artifact']), software='cp2k',
                                      target=request['identity']['target'])
    if manifest['identity'] != request['identity'] or manifest['sha256'] != request['artifact_sha256']:
        raise ValueError('sealed artifact identity changed')
    return manifest


def prepare(args):
    control = args.controller.resolve(strict=True)
    if not control.is_relative_to(ROOT / 'controller'):
        raise ValueError('requires the original project frozen controller')
    fixtures, delivery, remote = controller_modules(control)
    reference = ROOT / 'runs' / remote.safe_name(args.build_run_id) / 'artifact.path'
    image = Path(reference.read_text().strip())
    manifest = delivery.load_artifact(ROOT, image, software='cp2k')
    identity = manifest['identity']
    if identity['source_sha'] != SOURCE_SHA or identity['target'] not in (
            'dsprhbm', '4v100-avx512', '16v100-avx2', '8v100v0-avx512'):
        raise ValueError('this supplementary evidence is pinned to the reviewed development source')
    if image != delivery.artifact_path(ROOT, identity, args.build_run_id):
        raise ValueError('build run/artifact mismatch')
    task = ROOT / 'runtime-tests' / remote.safe_name(args.run_id)
    if task.resolve() != task:
        raise ValueError('supplementary task directory must be canonical')
    task.mkdir(mode=0o700)
    (task / 'results').mkdir()
    modes = ['builtin', 'host'] + (['device'] if identity['gpus'] else [])
    hashes = {}
    for mode in ['inspect', *modes]:
        work = task / mode
        work.mkdir()
        (work / 'container-exec.sh').write_text(INNER)
        (work / 'cp2k-exec.sh').write_text(CHILD)
        if mode != 'inspect':
            original = fixtures.water_input()
            (work / 'input.inp').write_text(original if mode == 'builtin' else libxc_input(original, mode.upper()))
            for name, expected in fixtures.DATA_HASHES.items():
                content = subprocess.check_output(['git', '--git-dir=' + str(ROOT / 'cache/repositories/cp2k'),
                                                  'show', fixtures.FIXTURE_REVISION + ':data/' + name])
                (work / name).write_bytes(content)
                if sha(work / name) != expected:
                    raise ValueError('pinned fixture data differs: ' + name)
        for path in work.iterdir():
            hashes[str(path.relative_to(task))] = sha(path)
    (task / 'runtime.sh').write_text(instrument_launcher(control))
    (task / 'runtime.sh').chmod(0o700)
    shutil.copyfile(__file__, task / 'libxc_smoke.py')
    hashes['runtime.sh'] = sha(task / 'runtime.sh')
    hashes['libxc_smoke.py'] = sha(task / 'libxc_smoke.py')
    request = dict(schema=1, purpose='cp2k-development-libxc-supplement',
        identity=identity, build_run_id=args.build_run_id, artifact=str(image),
        artifact_sha256=manifest['sha256'], controller=str(control),
        original_launcher_sha256=sha(control / 'cp2k_runtime.sh'),
        controller_hashes={str(p.relative_to(control)): sha(p) for p in control.rglob('*')
                           if p.is_file() and '__pycache__' not in p.parts},
        script_hashes=hashes, modes=modes, tolerances=TOLERANCES,
        libxc_archive_sha256=LIBXC_SHA, fixture_revision=fixtures.FIXTURE_REVISION,
        kernel_pattern=LIBXC_KERNEL.pattern)
    q = shlex.quote
    target = identity['target']
    lines = ['#!/usr/bin/env bash', '#SBATCH --job-name=cp2k-libxc-' + target,
        '#SBATCH --partition=' + identity['partition'], '#SBATCH --qos=' + remote.project_qos(ROOT, target),
        '#SBATCH --nodes=1', '#SBATCH --ntasks=1', '#SBATCH --time=30', '#SBATCH --export=NIL',
        '#SBATCH --output=' + str(task / 'results/slurm-%j.log'),
        *remote.project_batch_directives(ROOT, task)]
    lines += ['#SBATCH --gpus-per-node=1'] if identity['gpus'] else ['#SBATCH --cpus-per-task=2']
    lines += ['set -eo pipefail', 'umask 077', 'export HOME=' + q(str(task)),
        'export USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?}',
        'export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
        'export LD_LIBRARY_PATH="" LD_PRELOAD="" PYTHONDONTWRITEBYTECODE=1',
        'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
        'export SAI_SOFTWARE_ROOT=' + q(str(ROOT)), 'export SAI_CP2K_IMAGE=' + q(str(image)),
        'export TMPDIR=' + q(str(ROOT / 'runtime' / args.run_id)),
        'mkdir -p "$TMPDIR"', 'source /etc/profile.d/lmod.sh', 'module purge',
        'module use /opt/modules/modulefiles/devtools /opt/modules/modulefiles/apps',
        'module load apptainer/1.4.4 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto']
    if identity['gpus']:
        lines += ['source /opt/sai_config/mps_mapping.d/' + identity['partition'] + '.bash']
    else:
        lines += ['export MAP_OPT=ppr:1:node:pe=2']
    lines += ['set -u', 'export OMP_NUM_THREADS=1 OMP_PLACES=cores OMP_PROC_BIND=close SLURM_EXPORT_ENV=ALL',
        'export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher',
        'scontrol show job -o "$SLURM_JOB_ID" > results/allocation.txt',
        'module -t list > results/modules.txt 2>&1', 'hostname -f > results/node.txt',
        'python3 ' + q(str(task / 'libxc_smoke.py')) + ' execute ' + q(str(task))]
    (task / 'job.sbatch').write_text('\n'.join(lines) + '\n')
    for filename in ('job.sbatch', 'runtime.sh'):
        subprocess.run(['bash', '-n', str(task / filename)], check=True)
    hashes['job.sbatch'] = sha(task / 'job.sbatch')
    write_json(task / 'request.json', request)
    print(task / 'job.sbatch')


def check_inputs(task, request):
    control = Path(request['controller'])
    for relative, expected in request['controller_hashes'].items():
        if sha(control / relative) != expected:
            raise ValueError('frozen controller changed: ' + relative)
    for relative, expected in request['script_hashes'].items():
        if sha(task / relative) != expected:
            raise ValueError('supplementary input changed: ' + relative)
    if request['tolerances'] != TOLERANCES or request['kernel_pattern'] != LIBXC_KERNEL.pattern:
        raise ValueError('supplementary acceptance thresholds changed')


def numerical(candidate, reference, bounds):
    energy = abs(candidate['energy_ha'] - reference['energy_ha'])
    force = max(abs(a - b) for x, y in zip(candidate['forces_ha_bohr'], reference['forces_ha_bohr'])
                for a, b in zip(x, y))
    if energy > bounds['energy_ha'] or force > bounds['force_ha_bohr']:
        raise ValueError('LibXC energy/force tolerance exceeded: ' + str((energy, force)))
    return dict(energy_delta_ha=energy, max_force_delta_ha_bohr=force, tolerances=bounds)


def kernels(database):
    with sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True) as connection:
        rows = connection.execute('''SELECT s.value, COUNT(*), SUM(k.end-k.start)
            FROM CUPTI_ACTIVITY_KIND_KERNEL k JOIN StringIds s ON s.id=k.demangledName
            GROUP BY s.value''').fetchall()
    matched = [dict(name=name, instances=count, total_gpu_ns=duration)
               for name, count, duration in rows if LIBXC_KERNEL.search(name) and count > 0 and duration > 0]
    if not matched:
        raise ValueError('profile has no positive LibXC GGA CUDA kernel evidence')
    return matched


def verify(task, request, fixtures, delivery):
    check_inputs(task, request)
    artifact(request, delivery)
    inspect = task / 'inspect'
    if LIBXC_SHA not in (inspect / 'libxc-source-archives.sha256').read_text():
        raise ValueError('private LibXC source archive differs')
    ldd = (inspect / 'ldd.txt').read_text()
    if 'not found' in ldd:
        raise ValueError('unresolved runtime library')
    gpu = request['identity']['gpus'] > 0
    if gpu and not re.search(r'^CP2K_ENABLE_LIBXC_GPU:BOOL=ON$', (inspect / 'CMakeCache.txt').read_text(), re.M):
        raise ValueError('CP2K LibXC GPU backend was not enabled')
    science, loader = {}, {}
    for mode in request['modes']:
        work = task / mode
        if (work / 'cp2k.exit').read_text().strip() != '0':
            raise ValueError('CP2K failed: ' + mode)
        log = (work / 'cp2k.log').read_text()
        if mode != 'builtin' and not re.search(r'FUNCTIONAL\| LIBXC version\s+7\.1\.2\s*$', log, re.M):
            raise ValueError('executed LibXC version differs: ' + mode)
        if 'FUNCTIONAL| LIBXC compiled against version' in log:
            raise ValueError('LibXC runtime/header version mismatch: ' + mode)
        science[mode] = fixtures.parse_science(log,
            (work / 'forces.xyz').read_text(), (work / 'elapsed.txt').read_text(), 'water-gpw')
        if gpu:
            expected = request['identity']['install_prefix'] + '/dependencies/libxc/lib/libxc'
            loaded = [line.strip() for path in work.glob('loader.*') for line in path.read_text().splitlines()
                      if 'calling init:' in line and expected in line]
            if not loaded or expected not in ldd:
                raise ValueError('private LibXC loader proof missing: ' + mode)
            loader[mode] = sorted(set(loaded))
    comparisons = {'builtin_host': numerical(science['host'], science['builtin'], TOLERANCES['builtin_host'])}
    gpu_kernels = []
    if gpu:
        if not (inspect / 'libxc-libraries.sha256').read_text().strip():
            raise ValueError('private shared LibXC hashes missing')
        comparisons['host_device'] = numerical(science['device'], science['host'], TOLERANCES['host_device'])
        gpu_kernels = kernels(task / 'device/libxc-device.sqlite')
    result = dict(status='passed', purpose=request['purpose'], identity=request['identity'],
        artifact=request['artifact'], artifact_sha256=request['artifact_sha256'],
        request_sha256=sha(task / 'request.json'), job_id=os.environ.get('SLURM_JOB_ID'),
        science=science, comparisons=comparisons, libxc_loader=loader, libxc_gpu_kernels=gpu_kernels,
        libxc_version='7.1.2', cpu_static_provider=not gpu, main_science_acceptance_modified=False,
        output_sha256={str(path.relative_to(task)): sha(path)
                       for name in ['inspect', *request['modes'], 'results']
                       for path in (task / name).iterdir() if path.is_file()
                       and not path.name.startswith('slurm-')
                       and str(path.relative_to(task)) not in request['script_hashes']})
    write_json(task / 'results/accepted.json', result)
    print(json.dumps(result, sort_keys=True))


def execute(task):
    if task.resolve() != task or not task.is_relative_to(ROOT / 'runtime-tests'):
        raise ValueError('execution must stay inside the project runtime-tests')
    request = json.loads((task / 'request.json').read_text())
    fixtures, delivery, _ = controller_modules(Path(request['controller']))
    check_inputs(task, request)
    artifact(request, delivery)
    if os.environ.get('SLURM_JOB_ACCOUNT') not in (None, 'proj-sai-hpc-software'):
        raise ValueError('incorrect allocation account')
    if os.environ.get('SLURM_JOB_PARTITION') != request['identity']['partition']:
        raise ValueError('incorrect allocation partition')
    allocation = (task / 'results/allocation.txt').read_text()
    if not re.search(r'(?:^|\s)Account=proj-sai-hpc-software(?:\s|$)', allocation):
        raise ValueError('allocation does not use the project account')
    if not re.search(r'(?:^|\s)WorkDir=' + re.escape(str(task)) + r'(?:\s|$)', allocation):
        raise ValueError('allocation does not use the project working directory')
    for mode in ['inspect', *request['modes']]:
        work = task / mode
        env = dict(os.environ, SAI_LIBXC_MODE=mode)
        argv = ['mpirun', '-np', '1', '--map-by', env['MAP_OPT'], '--report-bindings', str(task / 'runtime.sh')]
        if mode != 'inspect':
            argv += ['-i', 'input.inp']
        start = time.monotonic()
        with (work / 'cp2k.log').open('x') as stream:
            subprocess.run(argv, cwd=work, env=env, stdout=stream, stderr=subprocess.STDOUT, check=True)
        (work / 'elapsed.txt').write_text(str(time.monotonic() - start) + '\n')
    verify(task, request, fixtures, delivery)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--controller', type=Path, required=True)
    prep.add_argument('--build-run-id', required=True)
    prep.add_argument('--run-id', required=True)
    run = commands.add_parser('execute')
    run.add_argument('task', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args)
    else:
        try:
            execute(args.task)
        except Exception as error:
            failed = args.task / 'results/failed.json'
            if failed.parent.is_dir() and not failed.exists():
                write_json(failed, dict(status='failed', error=str(error), job_id=os.environ.get('SLURM_JOB_ID')))
            raise


if __name__ == '__main__':
    main()
