#!/usr/bin/env python3
"""Prepare upstream inputs as data; execute the upstream harness only in the SIF."""
import argparse
import csv
import difflib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

try:
    import tomllib
except ImportError:
    from pip._vendor import tomli as tomllib

from cp2k_feature_contract import check_flags

# Keep backend-specific ELPA variants out; retain the two generic block sizes.
# None means this case exercises a fallback, not a cuSOLVERMp eigensolve.
CASES = {
    'regtest-cusolver': {'H2O-6.inp': None,
        'Si8-generalized.inp': 'cp_fm_general_cusolver',
        'Si8-generalized-complex.inp': 'cp_cfm_general_cusolver'},
    'regtest-elpa': {'cplx_kp.inp': None,
        'tailblock_hang.inp': 'cp_fm_diag_cusolver',
        'H13-tailblock.inp': 'cp_fm_diag_cusolver'},
    'regtest-elpa-2': {'H2O-elpa-redistribute.inp': None},
    'regtest-elpa-qr': {'si128-elpa.inp': 'cp_fm_diag_cusolver',
        'si128-elpa-qr.inp': 'cp_fm_diag_cusolver'},
}
HARNESS = ('do_regtest.py', 'matchers.py', 'matcher_classes.py',
           'SLOW_TESTS_SUPPRESSIONS', 'HUGE_TESTS_SUPPRESSIONS')
MATCHERS = {'E_total': ('Total energy:', 3),
            'M011': ('ENERGY| Total FORCE_EVAL', 9), 'M095': ('Fermi energy:', 3)}
TIMER = re.compile(r'^\s*(cp_(?:fm_diag|fm_general|cfm_general)_cusolver)\s+(\d+)\s+', re.M)
RESOURCES = dict(nodes=1, ranks=4, omp_threads=4, gpus_per_node=4)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_file(root, relative, files=None):
    path = root / relative
    if (not path.is_file() or path.is_symlink() or path.resolve() != path or
            not path.is_relative_to(root)):
        raise ValueError('missing or redirected test evidence: ' + str(path))
    data = path.read_bytes()
    if files is not None:
        files[str(relative)] = digest(data)
    return data.decode()


def specs(text):
    result = tomllib.loads(text)
    for checks in result.values():
        for check in checks:
            if (set(check) != {'matcher', 'tol', 'ref'} or check['matcher'] not in MATCHERS or
                    any(isinstance(check[k], bool) or not isinstance(check[k], (int, float)) or
                        not math.isfinite(check[k]) for k in ('ref', 'tol')) or check['tol'] < 0):
                raise ValueError('upstream matcher changed; review the tester coverage')
    return result


def prepare(read_source, destination=None):
    """read_source reads this candidate's Git tree, never a moving branch name."""
    destination = Path(destination) if destination is not None else None
    files, differences = {}, []

    def write(relative, data):
        if destination is not None:
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        files[str(relative)] = digest(data)

    def upstream(relative):
        data = read_source('tests/' + relative)
        write('upstream/tests/' + relative, data)
        return data

    for name in (*HARNESS, 'QS/sample_xyz/SI_8.xyz'):
        write('tests/' + name, upstream(name))
    write('tests/TEST_DIRS', ''.join(f'QS/{d} cusolvermp\n' for d in CASES).encode())
    for directory, inputs in CASES.items():
        original_specs = specs(upstream(f'QS/{directory}/TEST_FILES.toml').decode())
        selected_specs = []
        for name in inputs:
            relative = f'QS/{directory}/{name}'
            before = upstream(relative).decode()
            after = before
            if directory != 'regtest-cusolver':
                after = re.sub(r'^\s*ELPA_\w+[^\n]*\n', '', after, flags=re.M)
                after, count = re.subn(r'(PREFERRED_DIAG_LIBRARY\s+)ELPA\b', r'\1CUSOLVER', after)
                if count == 0:
                    if name != 'tailblock_hang.inp' or after.count('&GLOBAL\n') != 1:
                        raise ValueError('upstream solver selection changed: ' + relative)
                    after = after.replace('&GLOBAL\n', '&GLOBAL\n  PREFERRED_DIAG_LIBRARY CUSOLVER\n', 1)
            write('tests/' + relative, after.encode())
            differences += difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                fromfile='upstream/tests/' + relative, tofile='tests/' + relative)
            checks = ['{matcher=' + json.dumps(c['matcher']) + ', tol=' + repr(c['tol']) +
                      ', ref=' + repr(c['ref']) + '}' for c in original_specs[name]]
            selected_specs.append(json.dumps(name) + ' = [' + ', '.join(checks) + ']\n')
        text = ''.join(selected_specs)
        if specs(text) != {name: original_specs[name] for name in inputs}:
            raise ValueError('matcher/reference/tolerance changed')
        write(f'tests/QS/{directory}/TEST_FILES.toml', text.encode())
    write('adaptations.diff', ''.join(differences).encode())
    return files


def numeric_result(output, check):
    pattern, column = MATCHERS[check['matcher']]
    rows = [line for line in output.splitlines() if pattern in line]
    if not rows:
        raise ValueError('missing numerical result: ' + check['matcher'])
    value = float(rows[-1].split()[column - 1].replace('D', 'E'))
    error = abs((value - check['ref']) / check['ref'] if check['ref'] else value)
    if not math.isfinite(value) or error > check['tol']:
        raise ValueError(f'{check["matcher"]}: difference {error} exceeds {check["tol"]}')
    return dict(check, value=value, relative_error=error)


def verify_results(task, request):
    """Recheck numerical outputs without importing upstream code on the host."""
    files = {}
    for relative, expected in request['suite_files'].items():
        read_file(task, relative, files)
        if files[relative] != expected:
            raise ValueError('regression inputs or harness changed: ' + relative)
    flags = check_flags(read_file(task, 'results/version.txt', files), request['target'])
    # These are also present in the accepted development build, beyond the
    # cross-release floor. Preserve them in this user-facing development SIF.
    missing = {'libxc_gpu', 'libwignernj', 's_dftd3', 'openblas'} - set(flags)
    if missing:
        raise ValueError('development CP2K feature loss: ' + ', '.join(sorted(missing)))
    if read_file(task, 'results/harness-exit-code', files).strip() != '0':
        raise ValueError('upstream regression harness failed')
    report = read_file(task, 'results/regtest.log', files)
    if re.findall(r'^Status: (\S+)$', report, re.M) != ['OK']:
        raise ValueError('upstream regression status is not OK')
    workdirs = list((task / 'results/regtesting').glob('TEST-*'))
    if len(workdirs) != 1:
        raise ValueError('expected exactly one fresh upstream regression run')
    devices = json.loads(read_file(task, 'results/devices.json', files))
    if len(devices) != 4 or len(set(devices)) != 4:
        raise ValueError('expected four distinct allocated GPUs')
    results, checks = [], 0
    for directory, inputs in CASES.items():
        definitions = specs(read_file(task, f'tests/QS/{directory}/TEST_FILES.toml'))
        for name, timer in inputs.items():
            folder = workdirs[0] / 'QS' / directory
            relative = str(folder.relative_to(task)) + '/' + name
            original = f'tests/QS/{directory}/{name}'
            if digest(read_file(task, relative, files).encode()) != request['suite_files'][original]:
                raise ValueError('executed input differs from its prepared upstream case')
            output = read_file(task, relative + '.out', files)
            if 'PROGRAM ENDED AT' not in output or re.search(r'\bABORT\b', output):
                raise ValueError('CP2K did not finish: ' + name)
            revisions = re.findall(r'CP2K\| source code revision number:\s*(\S+)', output)
            if len(revisions) != 1 or revisions[0] != request['source_sha'][:len(revisions[0])] or len(revisions[0]) < 7:
                raise ValueError('executed CP2K source differs from candidate')
            timers = {key: int(count) for key, count in TIMER.findall(output)}
            if timer and timers.get(timer, 0) < 1:
                raise ValueError('cuSOLVERMp was not executed: ' + name)
            numerical = [numeric_result(output, check) for check in definitions[name]]
            checks += max(1, len(numerical))
            used, assigned = set(), []
            for rank in range(4):
                trace = json.loads(read_file(task, str(folder.relative_to(task)) + f'/rank-{name}-{rank}.json', files))
                cores = {tuple(c) for c in trace['cores']}
                if (trace['rank'] != rank or trace['size'] != 4 or trace['omp_threads'] != 4 or
                        trace['binary'] != request['identity']['install_prefix'] + '/bin/cp2k.psmp' or
                        trace['image_sha256'] != request['artifact_sha256'] or
                        len(cores) != 4 or cores & used):
                    raise ValueError('incorrect rank placement or candidate identity')
                used |= cores
                assigned.append(trace['gpu'])
            if set(assigned) != set(devices):
                raise ValueError('ranks did not use the four allocated GPUs')
            results.append(dict(input=f'QS/{directory}/{name}', matchers=numerical, timers=timers,
                                coverage=timer or 'fallback', physical_cores=len(used)))
    for kind, expected in (('FAILED', 0), ('WRONG', 0), ('CORRECT', checks)):
        if re.findall(r'^Number of\s+' + kind + r'\s+tests\s+(\d+)$', report, re.M) != [str(expected)]:
            raise ValueError('regression report count differs from the verified cases')
    return dict(cases=results, reported_checks=checks,
                numerical_checks=sum(len(case['matchers']) for case in results),
                flags=flags, devices=devices, files=files)


def input_name(argv):
    if argv == ['--version']:
        return 'version'
    name = argv[1] if len(argv) == 2 and argv[0] == '-i' else argv[0] if len(argv) == 1 else ''
    if not re.fullmatch(r'[A-Za-z0-9_.-]+\.inp', name):
        raise ValueError('unexpected upstream input filename')
    return name


def rank():
    rank_id = int(os.environ['OMPI_COMM_WORLD_RANK'])
    local_rank = int(os.environ['OMPI_COMM_WORLD_LOCAL_RANK'])
    devices = json.loads(os.environ['SAI_REGTEST_DEVICES'])
    os.environ['CUDA_VISIBLE_DEVICES'] = devices[local_rank]
    name = input_name(sys.argv[1:])
    cores = set()
    for cpu in os.sched_getaffinity(0):
        topology = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
        cores.add(tuple(int((topology / key).read_text()) for key in ('physical_package_id', 'core_id')))
    binary = os.environ['SAI_REGTEST_BINARY']
    record = dict(rank=rank_id, size=int(os.environ['OMPI_COMM_WORLD_SIZE']), cores=sorted(cores),
        gpu=devices[local_rank], omp_threads=int(os.environ['OMP_NUM_THREADS']), binary=binary,
        image_sha256=os.environ['SAI_REGTEST_IMAGE_SHA256'])
    Path(f'rank-{name}-{rank_id}.json').write_text(json.dumps(record) + '\n')
    os.execv(binary, [binary, *sys.argv[1:]])


def run(task, mpirun):
    task = Path(task).resolve()
    request = json.loads((task / 'request.json').read_text())
    prefix = Path(request['identity']['install_prefix'])
    binary = prefix / 'bin/cp2k.psmp'
    error = None
    try:
        if not os.environ.get('APPTAINER_CONTAINER') or not Path('/.singularity.d').is_dir():
            raise ValueError('the regression harness must execute inside the candidate SIF')
        if json.loads((prefix / 'share/sai/release-identity.json').read_text()) != request['identity']:
            raise ValueError('container installation differs from candidate identity')
        version = subprocess.check_output([str(binary), '--version'], text=True, stderr=subprocess.STDOUT)
        (task / 'results/version.txt').write_text(version)
        check_flags(version, request['target'])
        visible = os.environ['CUDA_VISIBLE_DEVICES']
        rows = subprocess.check_output(['nvidia-smi', '-i', visible, '--query-gpu=uuid',
            '--format=csv,noheader'], text=True)
        devices = [row[0].strip() for row in csv.reader(rows.splitlines())]
        if len(devices) != 4 or len(set(devices)) != 4:
            raise ValueError('Slurm must expose exactly four allocated GPUs')
        (task / 'results/devices.json').write_text(json.dumps(devices) + '\n')
        env = dict(os.environ, SAI_REGTEST_BINARY=str(binary), SAI_REGTEST_DEVICES=json.dumps(devices),
                   SAI_REGTEST_IMAGE_SHA256=request['artifact_sha256'], OMP_NUM_THREADS='4',
                   OPENBLAS_NUM_THREADS='4', OMP_PLACES='cores', OMP_PROC_BIND='close', OMP_DYNAMIC='FALSE')
        # All ranks live in this single-node container and share its namespace.
        # Prevent PRRTE from launching host-side Slurm daemons outside the SIF.
        mpi = (str(mpirun) + ' -np {N} --mca plm isolated --mca ras ^slurm --host localhost:16'
               ' --map-by ppr:4:node:pe=4 --bind-to core --report-bindings')
        command = [sys.executable, '-u', str(task / 'tests/do_regtest.py'), str(task / 'bin'), 'psmp',
            '--skip_unittests', '--mpiranks', '4', '--ompthreads', '4', '--maxtasks', '16',
            '--num_gpus', '4', '--timeout', '150', '--mpiexec', mpi,
            '--workbasedir', str(task / 'results/regtesting'), '--cp2kdatadir', str(prefix / 'share/cp2k/data')]
        with (task / 'results/regtest.log').open('w') as stream:
            code = subprocess.call(command, cwd=task, env=env, stdout=stream, stderr=subprocess.STDOUT)
        (task / 'results/harness-exit-code').write_text(str(code) + '\n')
        verify_results(task, request)
    except Exception as exc:
        error = str(exc)
        print(error, file=sys.stderr, flush=True)
    finally:
        write_report(task, request, error)
    return 1 if error else 0


def write_report(task, request, error=None):
    raw = task / 'results/regtest.log'
    content = (f'CommitSHA: {request["source_sha"]}\nTester: SAI cuSOLVERMp (4 V100)\n'
               f'ImageSHA256: {request["artifact_sha256"]}\nRecipeSHA256: {request["recipe_sha256"]}\n')
    content += raw.read_text() if raw.exists() else ''
    summary = ('FAILED: ' + ' '.join(str(error).split()) if error else
               '9 cases passed; 6 cuSOLVERMp paths verified')
    content += f'\nSummary: {summary}\nStatus: {"FAILED" if error else "OK"}\n'
    (task / 'results/report.txt').write_text(content)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task', type=Path)
    parser.add_argument('mpirun', type=Path)
    args = parser.parse_args()
    raise SystemExit(run(args.task, args.mpirun))
