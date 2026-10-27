#!/usr/bin/env python3
"""Paired installed-release/SIF measurements; every trial retains raw outputs."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import time


def read(path):
    return json.loads(Path(path).read_text())


def write(path, data):
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + '\n')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def schedule(repeats):
    yield 'warmup', 'native'
    yield 'warmup', 'sif'
    for i in range(repeats):
        for route in (('native', 'sif') if i % 2 == 0 else ('sif', 'native')):
            yield str(i), route


def summarize(samples, repeats):
    result = {}
    for case in sorted({r['case'] for r in samples if r['repeat'].isdigit()}):
        rows = [r for r in samples if r['case'] == case and r['repeat'].isdigit()]
        groups = {route: sorted((r for r in rows if r['route'] == route), key=lambda r: int(r['repeat']))
                  for route in ('native', 'sif')}
        assert all(len(g) == repeats for g in groups.values()), case
        assert all([r['repeat'] for r in g] == [str(i) for i in range(repeats)]
                   for g in groups.values()), ('missing or duplicate repetition', case)
        for key in ('iterations', 'steps', 'evaluations', 'atoms'):
            if key in rows[0]:
                assert all(r[key] == rows[0][key] for r in rows), ('different workload', case, key)
        entry = {}
        for metric in ('wall_seconds', 'compute_seconds'):
            if any(metric not in r for r in rows):
                continue
            values = {route: [r[metric] for r in g] for route, g in groups.items()}
            assert all(math.isfinite(v) and v > 0 for values_ in values.values() for v in values_)
            medians = {route: statistics.median(v) for route, v in values.items()}
            deltas = [a - b for a, b in zip(values['sif'], values['native'])]
            entry[metric] = dict(samples=values, medians=medians,
                ratio=medians['sif'] / medians['native'],
                percent_change=100 * (medians['sif'] / medians['native'] - 1),
                paired_deltas=deltas, paired_delta_median=statistics.median(deltas))
        result[case] = entry
    return result


def parse(folder, case, plan, wall):
    text = (folder / 'stdout.txt').read_text()
    value = dict(wall_seconds=wall)
    if case == 'startup':
        return value
    software = plan['software']
    if software == 'cp2k':
        import cp2k_acceptance as cp
        result = cp.parse_science(text, (folder / 'forces.xyz').read_text(), str(wall), case)
        cp.compare_science(result, plan['references'][case])
        value.update(compute_seconds=result['cp2k_seconds'], numerical=result,
                     iterations=re.findall(r'SCF run converged in\s+(\d+) steps', text))
    elif software == 'abacus':
        from abacus_benchmark import parse_scf
        logs = list(folder.glob('OUT.*/running_scf.log'))
        assert len(logs) == 1, logs
        result = parse_scf(text, logs[0].read_text(), str(wall))
        assert abs(result['energy_ev'] - plan['references'][case]) < 1e-5
        value.update(compute_seconds=result['rounded_iteration_seconds'], numerical=result,
                     iterations=result['iterations'])
    elif software == 'lammps':
        from md_performance import parse_engine_time
        from md_performance_run import verify_large_science
        steps = int(re.findall(r'^run (\d+) pre no$', (folder / 'in.lmp').read_text(), re.M)[0])
        timing = parse_engine_time(text, steps)
        science = verify_large_science(text, (folder / 'result.dump').read_text(),
                                      (folder / 'COLVAR').read_text(), read(folder / 'fixture.json'))
        value.update(compute_seconds=timing['engine_seconds'], numerical=science,
                     steps=steps, atoms=timing['atoms'])
    else:
        result = read(folder / 'inference.json')
        assert result['passed'] and result['device'].startswith('cuda') and result['cuda_bytes'] > 0
        assert result['package'].startswith(plan['prefix'] + '/')
        value.update(compute_seconds=result['compute_seconds'], numerical=result,
                     evaluations=result['evaluations'], atoms=result['atoms'])
    assert value['compute_seconds'] > 0 and math.isfinite(value['compute_seconds'])
    return value


def check_probes(root, plan):
    records = {route: read(root / 'probes' / route / 'probe.json') for route in ('native', 'sif')}
    a, b = records['native'], records['sif']
    for key in ('uid', 'node', 'affinity', 'cuda_visible_devices', 'omp_threads'):
        assert a[key] == b[key], ('probe mismatch', key)
    assert a['binary']['sha256'] == b['binary']['sha256'] == plan['binary_sha256']
    # Apptainer exposes identical GPU driver bytes under .singularity.d/libs.
    # Retain full paths in evidence, compare SONAME basenames and file hashes.
    libraries = lambda r: sorted((Path(p).name, sha) for p, sha in r['libraries'].items())
    assert libraries(a) == libraries(b), 'linked library bytes differ'
    for p, expected in plan['probe_files'].items():
        assert a['files'][p]['sha256'] == b['files'][p]['sha256'] == expected, p
    app_file = plan['application_file']
    assert a['files'][app_file]['device'] != b['files'][app_file]['device'], 'application hidden by host bind'
    assert 'squashfs' in (root / 'probes/sif/mountinfo.txt').read_text() or 'squashfuse' in (root / 'probes/sif/mountinfo.txt').read_text()
    assert a['uid'] != 0
    return records


def execute(root):
    plan = read(root / 'plan.json')
    sys.path.insert(0, str(root / 'control/controller'))
    assert os.environ['SLURM_JOB_PARTITION'] == plan['partition']
    assert os.environ['MPI_HOME'] == plan['mpi_prefix']
    assert os.environ.get('CUDA_VISIBLE_DEVICES') not in (None, '', '-1')
    assert digest(plan['binary']) == plan['binary_sha256']
    assert digest(plan['image']) == plan['image_sha256']
    for relative, sha in plan['frozen_files'].items():
        assert digest(root / relative) == sha, relative
    # Both routes receive the actual native module environment. Only paths to
    # external dependencies are mounted; the application remains inside SIF.
    native_env = dict(os.environ)
    environment_file = root / 'native-env.json'
    write(environment_file, {k: v for k, v in native_env.items() if k in (
        'PATH', 'LD_LIBRARY_PATH', 'PYTHONPATH', 'MPI_HOME', 'OMP_NUM_THREADS',
        'CP2K_DATA_DIR', 'LAMMPS_POTENTIALS', 'PLUMED_KERNEL', 'MAP_OPT')})
    from sif_modules import install
    row = plan['runtime_row']
    exports = ('PATH', 'LD_LIBRARY_PATH', 'PYTHONPATH', 'CP2K_DATA_DIR',
               'LAMMPS_POTENTIALS', 'PLUMED_KERNEL', 'MPI_HOME', 'OPAL_PREFIX')
    row = dict(row, environment='\n'.join('export ' + k + '=' + shlex.quote(native_env[k])
               for k in exports if k in native_env) + '\n')
    row['environment_sha256'] = hashlib.sha256(row['environment'].encode()).hexdigest()
    snapshot = dict(schema=1, snapshot='20261027', memory_bootstrap=plan['memory_bootstrap'],
                    applications={plan['software']:dict(version='release-measurement',
                        partitions={plan['partition']:row})})
    install(snapshot, root / 'bundle')
    samples = []
    executable = root / 'control/deployment/release-sif-efficiency'
    direct_cpus = []
    if plan['software'] == 'deepmd-kit':
        cores = set()
        for line in subprocess.check_output(['lscpu', '-p=CPU,CORE,SOCKET'], text=True).splitlines():
            if line.startswith('#'):
                continue
            cpu, core, socket = map(int, line.split(','))
            if cpu in os.sched_getaffinity(0) and (socket, core) not in cores:
                direct_cpus.append(cpu)
                cores.add((socket, core))
        direct_cpus = direct_cpus[:int(native_env['OMP_NUM_THREADS'])]
        assert len(direct_cpus) == int(native_env['OMP_NUM_THREADS'])

    def invoke(folder, route, operation, args):
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(executable / 'observe.py', folder / 'observe.py')
        if operation == 'probe':
            write(folder / 'probe-files.json', list(plan['probe_files']))
        mpi = str(root / 'bundle/bin/mpi/mpirun') if route == 'sif' else plan['mpi_prefix'] + '/bin/mpirun'
        command = [mpi, '-np', '1', '--map-by', os.environ['MAP_OPT'], '--bind-to', 'core',
                   '--report-bindings', '/usr/bin/python3', '-B', str(executable / 'rank.py'),
                   str(root / 'plan.json'), route, operation, *args]
        if plan['software'] == 'deepmd-kit':
            python = plan['binary'] if route == 'native' else str(root / 'bundle/bin/deepmd-kit/deepmd-python')
            command = ['/usr/bin/taskset', '-c', ','.join(map(str, direct_cpus)),
                       python, '-B', str(folder / 'observe.py'), operation, plan['binary'], *args]
        write(folder / 'command.json', command)
        started = time.perf_counter()
        with (folder / 'stdout.txt').open('x') as stream:
            completed = subprocess.run(command, cwd=folder, env=native_env, stdout=stream,
                                       stderr=subprocess.STDOUT, timeout=1800)
        wall = time.perf_counter() - started
        write(folder / 'process.json', dict(returncode=completed.returncode, seconds=wall))
        assert completed.returncode == 0, str(folder)
        rank = read(folder / 'rank.json')
        assert (rank['cwd_device'], rank['cwd_inode']) == (folder.stat().st_dev, folder.stat().st_ino)
        return wall

    for route in ('native', 'sif'):
        invoke(root / 'probes' / route, route, 'probe', [])
    probes = check_probes(root, plan)
    write(root / 'provenance.json', probes)

    def trial(case, repeat, route, *, count=None):
        folder = root / 'cases' / case / repeat / route
        source = root / 'inputs' / case
        if source.exists():
            shutil.copytree(source, folder)
        else:
            folder.mkdir(parents=True)
        if case == 'startup':
            args = plan['startup']
        elif plan['software'] == 'cp2k':
            args = ['-i', 'input.inp']
        elif plan['software'] == 'abacus':
            args = []
        elif plan['software'] == 'lammps':
            from md_performance import render_input
            (folder / 'in.lmp').write_text(render_input('pt', count))
            args = ['-in', 'in.lmp']
        else:
            shutil.copyfile(executable / 'deepmd-inference.py', folder / 'inference.py')
            (folder / 'evaluations.txt').write_text(str(count))
            args = ['inference.py']
        # invoke creates only a new directory for probes; trials stage inputs first.
        wall = invoke(folder, route, 'run', args)
        row = dict(case=case, repeat=repeat, route=route, directory=str(folder), **parse(folder, case, plan, wall))
        rank = read(folder / 'rank.json')
        for key in ('uid', 'node', 'affinity', 'cuda_visible_devices', 'omp_threads'):
            assert rank[key] == probes[route][key], ('changed placement', key)
        samples.append(row)
        write(root / 'partial.json', samples)
        print(case, repeat, route, 'wall', wall, 'compute', row.get('compute_seconds'), flush=True)
        return row

    for case in ['startup', *plan['cases']]:
        count = None
        if case != 'startup' and plan['software'] in ('lammps', 'deepmd-kit'):
            count = 100
            for attempt in range(5):
                row = trial(case, 'calibration' + str(attempt), 'native', count=count)
                if row['compute_seconds'] >= 5:
                    break
                count = max(count + 1, math.ceil(count * 6 / row['compute_seconds']))
            assert row['compute_seconds'] >= 5, 'calibration interval too short'
            write(root / 'calibration.json', dict(count=count, last=row))
        for repeat, route in schedule(plan['repeats']):
            trial(case, repeat, route, count=count)
    # Retain first invocations separately, never describe them as cold-cache tests.
    report = dict(passed=True, directory=str(root), job=os.environ['SLURM_JOB_ID'], plan=plan, samples=samples,
                  summary=summarize(samples, plan['repeats']), provenance=probes,
                  scope='One process and one GPU; host MPI for CP2K/ABACUS/LAMMPS, direct DeePMD; same installed release; alternating pairs; no cache flush')
    assert set(report['summary']) == {'startup', *plan['cases']}
    report['files'] = {str(p.relative_to(root)): digest(p)
                       for d in ('cases', 'probes', 'bundle') for p in (root / d).rglob('*') if p.is_file()}
    for name in ('job.sbatch', 'plan.json', 'allocation.txt', 'modules.txt', 'native-env.json', 'provenance.json'):
        report['files'][name] = digest(root / name)
    write(root / 'report.json', report)
    print('RELEASE_SIF_EFFICIENCY_FINISHED', flush=True)


def verify(root, offline=False):
    plan, report = read(root / 'plan.json'), read(root / 'report.json')
    assert report['plan'] == plan and report['passed']
    original_root = Path(report.get('directory', str(Path(report['samples'][0]['directory']).parents[3])))
    accounting = root / 'accounting.txt'
    if not offline:
        assert root == original_root
        accounting.write_text(subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', report['job'],
            '-o', 'JobIDRaw,State,ExitCode,Partition,Account,WorkDir'], text=True))
    rows = accounting.read_text().splitlines()
    rows = [r.split('|') for r in rows if r.split('|')[0] == report['job']]
    assert len(rows) == 1 and rows[0][1:6] == ['COMPLETED', '0:0', plan['partition'],
        'proj-sai-hpc-software', str(original_root)], rows
    for relative, sha in plan['frozen_files'].items():
        assert digest(root / relative) == sha, relative
    assert check_probes(root, plan) == report['provenance']
    for relative, sha in report['files'].items():
        assert digest(root / relative) == sha, relative
    for row in report['samples']:
        relative = Path('cases') / row['case'] / row['repeat'] / row['route']
        assert Path(row['directory']) == original_root / relative
        folder = root / relative
        process = read(folder / 'process.json')
        assert process['returncode'] == 0 and process['seconds'] == row['wall_seconds']
        rank = read(folder / 'rank.json')
        for key in ('uid', 'node', 'affinity', 'cuda_visible_devices', 'omp_threads'):
            assert rank[key] == report['provenance'][row['route']][key]
        source = root / 'inputs' / row['case']
        if source.exists():
            for original in source.rglob('*'):
                if original.is_file():
                    assert digest(folder / original.relative_to(source)) == digest(original)
        sys.path.insert(0, str(root / 'control/controller'))
        parsed = parse(folder, row['case'], plan, row['wall_seconds'])
        assert all(row[k] == v for k, v in parsed.items())
    assert summarize(report['samples'], plan['repeats']) == report['summary']
    assert set(report['summary']) == {'startup', *plan['cases']}
    result = dict(passed=True, job=report['job'], software=plan['software'], partition=plan['partition'],
                  summary=report['summary'], report_sha256=digest(root / 'report.json'))
    write(root / 'verification.json', result)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('run', 'verify'))
    parser.add_argument('directory', type=Path)
    parser.add_argument('--offline', action='store_true', help='verify archived outputs and saved terminal accounting')
    args = parser.parse_args()
    if args.action == 'verify':
        verify(args.directory.resolve(), args.offline)
    else:
        assert not args.offline
        execute(args.directory.resolve())
