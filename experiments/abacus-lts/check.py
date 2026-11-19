#!/usr/bin/env python3
"""Run small upstream LTS cases through host MPI and the built read-only SIF.

Invoke in a Slurm allocation on the build's partition, after recording the
successful build with software_controller.py monitor. This is a one-rank
scientific smoke check, not multi-node or performance acceptance.
"""
import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys


def run(build, attempt=1):
    root = Path('/org/org-open-sai/proj-sai-hpc-software')
    build = Path(build).resolve()
    if build.parent != root / 'runs':
        raise ValueError('build must stay in the project run directory')
    request = json.loads((build / 'request.json').read_text())
    identity = request['identity']
    if identity['source_ref'] != 'LTS' or os.environ['SLURM_JOB_PARTITION'] != identity['partition']:
        raise ValueError('LTS source and native partition required')
    sys.path.insert(0, request['controller'])
    from delivery_layout import load_artifact
    image = Path((build / 'artifact.path').read_text().strip())
    manifest = load_artifact(root, image, software='abacus', target=identity['target'])
    if manifest['identity'] != identity:
        raise ValueError('build and artifact identity differ')
    if not 1 <= attempt <= 9:
        raise ValueError('invalid check attempt')
    suffix = '' if attempt == 1 else f'-r{attempt}'
    task = root / 'runtime-tests' / (build.name + '-lts-science' + suffix)
    task.mkdir(exist_ok=True)
    for name in ('results', 'mpi-runtime', 'apptainer-runtime'):
        (task / name).mkdir(exist_ok=True)
    environment = dict(os.environ, SAI_SOFTWARE_ROOT=str(root), SAI_ABACUS_IMAGE=str(image),
                       TMPDIR=str(task / 'mpi-runtime'), APPTAINER_TMPDIR=str(task / 'apptainer-runtime'),
                       APPTAINER_CACHEDIR=str(task / 'apptainer-runtime'), OMP_NUM_THREADS='1')
    gpu = identity['target'] != 'dsprhbm'
    checks = [('pw', 'gpu' if gpu else 'cpu', 1e-5), ('hse', 'cpu', 1e-4), ('deepks', 'cpu', 1e-4)]
    if gpu:
        checks.append(('cusolvermp', 'gpu', 1e-5))
    record = dict(identity=identity, artifact=str(image), artifact_sha256=manifest['sha256'],
                  job=os.environ['SLURM_JOB_ID'], ranks=1, results=[], passed=False)
    output = task / 'results/verification.json'
    for name, device, tolerance in checks:
        case = task / name
        if case.exists():
            raise ValueError('scientific run already exists: ' + str(case))
        case.mkdir()
        row = dict(case=name, device=device, energy_tolerance_ev=tolerance, passed=False)
        try:
            command = ['apptainer', 'exec', '--cleanenv', '--no-home', '--no-mount',
                       'bind-paths,home,cwd,tmp,hostfs', '--pwd', '/work']
            for path in ('/usr', '/lib', '/lib64'):
                command += ['--bind', f'{path}:{path}:ro']
            command += ['--bind', f'{case}:/work:rw', str(image), '/usr/bin/cp', '-a',
                        identity['install_prefix'] + '/share/sai/lts-cases/' + name + '/.', '/work/']
            subprocess.run(command, env=environment, check=True, timeout=60)
            text = (case / 'INPUT').read_text()
            text = re.sub(r'^\s*device\s+.*$', '', text, flags=re.M) + '\ndevice ' + device + '\n'
            if name == 'cusolvermp':
                text = re.sub(r'^\s*ks_solver\s+.*$', '', text, flags=re.M) + 'ks_solver cusolvermp\n'
            (case / 'INPUT').write_text(text)
            reference = float(re.search(r'^etotref\s+([-+0-9.eE]+)',
                                        (case / 'result.ref').read_text(), re.M).group(1))
            command = ['mpirun', '-n', '1', '--map-by', 'ppr:1:node:pe=1', '--bind-to', 'core',
                       str(Path(request['controller']) / 'abacus')]
            with (task / 'results' / (name + '.log')).open('w') as stream:
                subprocess.run(command, cwd=case, env=environment, stdout=stream,
                               stderr=subprocess.STDOUT, check=True, timeout=300)
            text = (case / 'OUT.autotest/running_scf.log').read_text()
            values = re.findall(r'!FINAL_ETOT_IS\s+([-+0-9.eE]+)', text)
            energy = float(values[-1])
            converged = 'charge density convergence is achieved' in text.lower()
            device_executed = device == 'cpu' or bool(re.search(
                r'^\s*RUNNING WITH DEVICE\s*:\s*GPU\s*/\s*\S+', text, re.M))
            solver_executed = name != 'cusolvermp' or bool(re.search(
                r'^\s*DiagoCusolverMP\s+Diag_CusolverMP_gvd\s', text, re.M))
            row.update(energy_ev=energy, reference_energy_ev=reference,
                       absolute_error_ev=abs(energy-reference), converged=converged,
                       device_executed=device_executed,
                       solver_executed=solver_executed,
                       passed=math.isfinite(energy) and abs(energy-reference) <= tolerance
                              and converged and device_executed and solver_executed)
        except Exception as error:
            row['error'] = str(error)
        record['results'].append(row)
        output.write_text(json.dumps(record, indent=2) + '\n')
        print(json.dumps(row), flush=True)
    record['passed'] = all(row['passed'] for row in record['results'])
    output.write_text(json.dumps(record, indent=2) + '\n')
    return 0 if record['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('build')
    parser.add_argument('--attempt', type=int, default=1)
    args = parser.parse_args()
    raise SystemExit(run(args.build, args.attempt))
