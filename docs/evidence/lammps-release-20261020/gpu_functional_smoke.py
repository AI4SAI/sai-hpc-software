#!/usr/bin/env python3
"""Check explicit GPU/Kokkos force styles against the installed reference."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

PROJECT = Path('/home/stardust/sai-hpc-software')
ROOT = PROJECT / 'experimental/deepmd-lammps'
CONTROL = ROOT / 'controller/acceptance/21a8a3cc5d6b35feb322425d4d6be8be85134a63/lammps-release-20261020'
BUILDS = {
    '16V100': 'md-39403084059-1-2026-10-20-5fef92e054bacdaf-export',
    '4V100': 'md-39403117741-1-2026-10-20-9bf6ae82814537d7-export',
    '8V100V0': 'md-39427644750-1-2026-10-20-1dfd6f53c113abda',
}


def digest(path):
    return hashlib.file_digest(Path(path).open('rb'), 'sha256').hexdigest()


def write(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write('\n')


def data():
    spacing = (4 / 0.7) ** (1 / 3)
    length = 4 * spacing
    atoms = []
    for i in range(4):
        for j in range(4):
            for k in range(4):
                for basis in ((0, 0, 0), (0.5, 0.5, 0), (0.5, 0, 0.5), (0, 0.5, 0.5)):
                    index = len(atoms) + 1
                    coords = [(spacing * (v + b) + 0.02 * math.sin(index * (axis + 13))) % length
                              for axis, (v, b) in enumerate(zip((i, j, k), basis))]
                    atoms.append(f'{index} 1 {1 if index % 2 else -1} ' + ' '.join(f'{v:.17g}' for v in coords))
    return ('Deterministic neutral 256-atom GPU correctness fixture\n\n256 atoms\n1 atom types\n\n' +
            '\n'.join(f'0 {length:.17g} {axis}lo {axis}hi' for axis in 'xyz') +
            '\n\nMasses\n\n1 1.0\n\nAtoms # charge\n\n' + '\n'.join(atoms) + '\n')


def input_text(potential, mode):
    pair = 'lj/cut' if potential == 'lj' else 'lj/cut/coul/long'
    pair += {'reference': '', 'gpu': '/gpu', 'kokkos': '/kk'}[mode]
    kspace = '' if potential == 'lj' else 'kspace_style pppm' + ('/kk' if mode == 'kokkos' else '') + ' 1e-8\n'
    return ('units lj\natom_style charge\nboundary p p p\nread_data data.lmp\n' +
            f'pair_style {pair} 2.5\npair_coeff * * 1.0 1.0\n' + kspace +
            'neighbor 0.3 bin\nneigh_modify every 1 delay 0 check no\n'
            'timestep 0.0001\nfix integrate all nve\nthermo 5\nthermo_modify norm no\nrun 5\n'
            'print "SAI_GPU_OBS $(pe:%.17g) $(pxx:%.17g) $(pyy:%.17g) $(pzz:%.17g) $(pxy:%.17g) $(pxz:%.17g) $(pyz:%.17g)"\n'
            'write_dump all custom result.dump id fx fy fz modify sort id format float %.17g\n')


def values(case):
    stdout = (case / 'stdout.txt').read_text()
    matches = re.findall(r'^SAI_GPU_OBS (.*)$', stdout, re.M)
    if len(matches) != 1:
        raise ValueError('missing unique energy/pressure result')
    observables = list(map(float, matches[0].split()))
    lines = (case / 'result.dump').read_text().splitlines()
    if len(observables) != 7 or len(lines) != 265 or lines[3] != '256' or lines[8] != 'ITEM: ATOMS id fx fy fz':
        raise ValueError('incomplete GPU science output')
    forces = []
    for index, line in enumerate(lines[9:], 1):
        columns = line.split()
        if len(columns) != 4 or columns[0] != str(index):
            raise ValueError('incomplete or unordered force IDs')
        forces.extend(map(float, columns[1:]))
    if not all(math.isfinite(v) for v in observables + forces):
        raise ValueError('non-finite numerical output')
    return observables, forces


def verify(task):
    request = json.loads((task / 'request.json').read_text())
    if digest(Path(request['artifact'])) != request['artifact_sha256']:
        raise ValueError('candidate image changed')
    results = {}
    files = {}
    for potential in ('lj', 'coulomb-pppm'):
        cpu_reference = values(task / (potential + '-baseline-reference'))
        for side in ('baseline', 'candidate'):
            for mode in ('reference', 'gpu', 'kokkos'):
                name = f'{potential}-{side}-{mode}'
                case = task / name
                process = json.loads((case / 'process.json').read_text())
                if process['returncode'] != 0 or (case / 'in.lmp').read_text() != input_text(potential, mode):
                    raise ValueError('GPU smoke process failed or input changed')
                if (case / 'data.lmp').read_text() != data():
                    raise ValueError('GPU smoke geometry changed')
                # The GPU package intentionally uses mixed precision; Kokkos
                # uses double. Compare the mixed path to the installed mixed
                # path, and double paths to the installed double reference.
                # Keep all CPU differences visible; never widen tolerances to
                # disguise the baseline's own mixed-precision rounding.
                reference_name = potential + '-baseline-' + ('gpu' if mode == 'gpu' else 'reference')
                reference = values(task / reference_name)
                stdout = (case / 'stdout.txt').read_text()
                if mode == 'gpu' and ('(Mixed Precision)' not in stdout or '- Using acceleration for' not in stdout):
                    raise ValueError('expected GPU mixed-precision execution was not reported')
                if mode == 'kokkos' and ('KOKKOS mode' not in stdout or 'using double precision' not in stdout
                                         or potential != 'lj' and 'using double precision cuFFT' not in stdout):
                    raise ValueError('expected Kokkos double-precision/CUFFT execution was not reported')
                observed = values(case)
                errors = {}
                for field, actual, expected in zip(('energy_and_pressure', 'all_forces'), observed, reference):
                    for a, b in zip(actual, expected):
                        if abs(a - b) > 2e-6 + 2e-5 * abs(b):
                            raise ValueError(f'{name}: {field} differs: {a} vs {b}')
                    errors[field] = max(abs(a - b) for a, b in zip(actual, expected))
                cpu_errors = {field: max(abs(a-b) for a, b in zip(actual, expected))
                              for field, actual, expected in zip(('energy_and_pressure', 'all_forces'), observed, cpu_reference)}
                results[name] = dict(passed=True, reference=reference_name, max_absolute_error=errors,
                                     cpu_reference_max_absolute_error=cpu_errors)
                for path in case.iterdir():
                    if path.is_file():
                        files[str(path.relative_to(task))] = digest(path)
    return dict(passed=True, scope='GPU package and Kokkos CUDA LJ/PPPM numerical correctness; no speed claim',
                artifact=request['artifact'], artifact_sha256=request['artifact_sha256'],
                verification_helper_sha256=digest(Path(__file__)),
                executed_helper_sha256=request['helper_sha256'],
                tolerances=dict(atol=2e-6, rtol=2e-5), results=results, files=files)


def run(task):
    request = json.loads((task / 'request.json').read_text())
    if digest(Path(__file__)) != request['helper_sha256']:
        raise ValueError('frozen smoke helper changed')
    sys.path.insert(0, str(CONTROL))
    from md_science import require_execution_context
    require_execution_context()
    for potential in ('lj', 'coulomb-pppm'):
        for side in ('baseline', 'candidate'):
            for mode in ('reference', 'gpu', 'kokkos'):
                case = task / f'{potential}-{side}-{mode}'
                case.mkdir()
                (case / 'data.lmp').write_text(data())
                (case / 'in.lmp').write_text(input_text(potential, mode))
                options = {'reference': [], 'gpu': ['-pk', 'gpu', '1', 'split', '1.0'],
                           'kokkos': ['-k', 'on', 'g', '1', 't', '1', '-sf', 'kk',
                                      '-pk', 'kokkos', 'gpu/aware', 'off']}[mode]
                command = ['bash', str(CONTROL / 'md_runtime.sh'), 'lmp', *options, '-in', 'in.lmp', '-log', 'log.lammps']
                with (case / 'stdout.txt').open('x') as output:
                    result = subprocess.run(command, cwd=case, env=dict(os.environ, SAI_MD_IMPLEMENTATION=side),
                                            stdout=output, stderr=subprocess.STDOUT, timeout=600)
                write(case / 'process.json', dict(returncode=result.returncode, command=command,
                      job=os.environ['SLURM_JOB_ID'], node=os.uname().nodename))
                result.check_returncode()
    write(task / 'results/evidence.json', verify(task))


def submit(target):
    sys.path.insert(0, str(CONTROL))
    from md_acceptance_recovery import capture
    from md_publication import build_record
    build = BUILDS[target]
    delivery = json.loads((ROOT / 'runs' / build / 'request.json').read_text())['delivery']
    recovery = capture(ROOT, CONTROL, build, delivery, 'Explicit GPU/Kokkos functional acceptance')
    _, image, sha = build_record(build, controller_recovery=recovery)
    task = ROOT / 'runtime-tests' / (build + '-gpu-functional')
    task.mkdir()
    for name in ('results', 'runtime'):
        (task / name).mkdir()
    helper = task / 'gpu_functional_smoke.py'
    shutil.copyfile(__file__, helper)
    helper.chmod(0o444)
    lines = ['#!/usr/bin/env bash', '#SBATCH --export=NIL', '#SBATCH --partition=' + target,
             '#SBATCH --qos=' + ('flood-1o2gpu' if target == '16V100' else 'rush-1o2gpu'),
             '#SBATCH --gpus-per-node=1', '#SBATCH --nodes=1', '#SBATCH --ntasks=1',
             '#SBATCH --time=30', '#SBATCH --job-name=md-gpu-functional-' + target,
             '#SBATCH --output=' + str(task / 'results/slurm-%j.log'), 'set -euo pipefail',
             'export HOME=/home/stardust USER=stardust LOGNAME=stardust PATH=/usr/bin:/bin',
             'export LD_LIBRARY_PATH="" LD_PRELOAD="" PYTHONDONTWRITEBYTECODE=1',
             'export SAI_SOFTWARE_ROOT=' + shlex.quote(str(PROJECT)),
             'export SAI_MD_DELIVERY=' + shlex.quote(json.dumps(delivery, sort_keys=True)),
             'export SAI_MD_IMAGE=' + shlex.quote(str(image)),
             'export SAI_MD_TEST_CONTROL=' + shlex.quote(str(CONTROL)),
             'export TMPDIR=' + shlex.quote(str(task / 'runtime')),
             'export APPTAINER_TMPDIR="$TMPDIR" APPTAINER_CACHEDIR="$TMPDIR/cache"',
             'export OMP_NUM_THREADS=1 DP_INTRA_OP_PARALLELISM_THREADS=1 DP_INTER_OP_PARALLELISM_THREADS=1',
             'mkdir -p "$APPTAINER_CACHEDIR"', 'source /etc/profile.d/lmod.sh',
             'module use /opt/modules/modulefiles/devtools',
             'module load apptainer/1.4.4 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto',
             shlex.join(['/usr/bin/python3', str(helper), 'run', str(task)])]
    script = task / 'job.sbatch'
    script.write_text('\n'.join(lines) + '\n')
    subprocess.run(['bash', '-n', str(script)], check=True)
    write(task / 'request.json', dict(build_run=build, artifact=str(image), artifact_sha256=sha,
          controller_recovery=recovery, helper_sha256=digest(helper), script_sha256=digest(script)))
    env = dict(PATH='/usr/bin:/bin', HOME=str(Path.home()), USER='stardust', LOGNAME='stardust', SBATCH_EXPORT='ALL')
    job = subprocess.check_output(['sbatch', '--hold', '--parsable', str(script)], env=env, text=True).strip()
    assert job.isdigit()
    (task / 'job.id').write_text(job + '\n')
    subprocess.run(['scontrol', 'release', job], check=True)
    print(json.dumps(dict(job=job, target=target, task=str(task))))


if __name__ == '__main__':
    os.umask(0o077)
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('submit', 'run', 'verify'))
    parser.add_argument('target')
    args = parser.parse_args()
    if args.operation == 'submit':
        submit(args.target)
    elif args.operation == 'run':
        run(Path(args.target))
    else:
        print(json.dumps(verify(Path(args.target)), sort_keys=True))
