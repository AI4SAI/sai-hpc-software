#!/usr/bin/env python3
"""Prepare/reparse short numerical checks of the new Lmod launch route."""
import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys

PACKAGE = Path(__file__).resolve().parent
sys.path.insert(0, str(PACKAGE.parents[1] / 'controller'))
from sif_runtime import digest, require
from cp2k_benchmark import parse_science, compare_science
from abacus_benchmark import parse_scf
from gpumd_science import xyz, compare


def prepare(output, partition, catalog):
    require(not output.exists(), 'new check directory required')
    output.mkdir(parents=True)
    for name in ('tmp', 'trace', 'cp2k', 'abacus'):
        (output / name).mkdir(mode=0o700)
    plan = json.loads((PACKAGE / 'check-plan.json').read_text())[partition]
    for name, expected in plan['abacus_files'].items():
        source = Path(plan['abacus_input']) / name
        require(digest(source) == expected, 'ABACUS input changed: ' + name)
        dest = output / 'abacus' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    shutil.copyfile(plan['cp2k_input'], output / 'cp2k/input.inp')
    if partition != 'DSPRHBM':
        for software, names in [('lammps', ('in.lmp', 'data.lmp')),
                                ('gpumd', ('run.in', 'nep.txt', 'model.xyz', 'gold.xyz')),
                                ('deepmd', ('fixture.json', 'model.pth'))]:
            folder = output / software
            folder.mkdir()
            for name in names:
                shutil.copyfile(Path(plan[software + '_source']) / name, folder / name)
        shutil.copyfile(PACKAGE / 'deepmd-check.py', output / 'deepmd/check.py')
    record = dict(partition=partition, ranks=4 if partition == '16V100' else 2,
                  catalog=str(catalog), catalog_sha256=digest(catalog),
                  runtime_sha256=digest(catalog.parent / 'libexec/sif_runtime.py'), plan=plan,
                  files={str(p.relative_to(output)): digest(p) for p in output.rglob('*') if p.is_file()})
    (output / 'request.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')


def verify(output, *, catalog_override=None, write=True):
    request = json.loads((output / 'request.json').read_text())
    catalog = Path(catalog_override or request['catalog'])
    require(digest(catalog) == request['catalog_sha256'] and
            digest(catalog.parent / 'libexec/sif_runtime.py') == request['runtime_sha256'],
            'tested runtime/catalog changed')
    for name, expected in request['files'].items():
        require(digest(output / name) == expected, 'check input changed: ' + name)
    partition, plan = request['partition'], request['plan']
    for software in ('cp2k', 'abacus', 'lammps'):
        log = output / software / 'stdout.txt'
        if log.exists():
            require(not re.search(r'UCX\s+ERROR|unable to create shared memory|performance degradation',
                                  log.read_text(), re.I), 'MPI shared-memory transport failed: ' + software)
    cp2k = output / 'cp2k'
    science = parse_science((cp2k / 'stdout.txt').read_text(), (cp2k / 'forces.xyz').read_text(),
                            (cp2k / 'elapsed.txt').read_text(), 'water-gpw')
    report = {'cp2k': compare_science(plan['cp2k_reference'], science)}
    abacus = output / 'abacus'
    sample = parse_scf((abacus / 'stdout.txt').read_text(), (abacus / 'OUT.autotest/running_scf.log').read_text(),
                       (abacus / 'elapsed.txt').read_text())
    error = abs(sample['energy_ev'] - plan['abacus_energy_ev'])
    require(error < 1e-5, 'ABACUS energy changed through Lmod/SIF route')
    report['abacus'] = dict(energy_error_ev=error, **sample)
    if partition != 'DSPRHBM':
        folder = output / 'lammps'
        log = (folder / 'stdout.txt').read_text()
        require('(Mixed Precision)' in log and '- Using acceleration for' in log, 'LAMMPS GPU was not used')
        [line] = re.findall(r'^SAI_GPU_OBS (.+)$', log, re.M)
        values = [float(v) for v in line.split()]
        dump = (folder / 'result.dump').read_text().split('ITEM: ATOMS id fx fy fz\n')[1]
        values.extend(float(v) for row in dump.splitlines() for v in row.split()[1:])
        expected = [v for group in plan['lammps_reference'] for v in group]
        require(len(values) == len(expected), 'LAMMPS force dimensions changed')
        require(all(math.isfinite(a) and abs(a - b) <= 2e-6 + 2e-5 * abs(b)
                    for a, b in zip(values, expected)), 'LAMMPS GPU observables changed')
        report['lammps'] = dict(max_error=max(abs(a-b) for a,b in zip(values, expected)), values=len(values))
        e, f = xyz(output / 'gpumd/dump.xyz')
        ge, gf = xyz(output / 'gpumd/gold.xyz')
        report['gpumd'] = dict(energy_error=compare([[e]], [[ge]], 1e-3, 'GPUMD energy'),
                               force_error=compare(f, gf, 1e-3, 'GPUMD forces'))
        report['deepmd-kit'] = json.loads((output / 'deepmd/result.json').read_text())
        require(report['deepmd-kit']['passed'], 'DeePMD inference failed')
    traces = [json.loads(p.read_text()) for p in (output / 'trace').glob('*.json')]
    applications = json.loads(catalog.read_text())['applications']
    for software in report:
        rows = [t for t in traces if t['software'] == software]
        require(rows and all(t['sha256'] == applications[software]['partitions'][partition]['sha256'] for t in rows),
                'wrong image selected through module')
        if software in ('cp2k', 'abacus', 'lammps'):
            require({t['rank'] for t in rows} == {str(i) for i in range(request['ranks'])}, 'missing host MPI rank')
            required_nodes = 2 if partition == '16V100' else 1
            require(len({t['node'] for t in rows}) == required_nodes, 'wrong MPI node placement')
            require(all(t['mpi'] == applications[software]['partitions'][partition]['mpi_prefix'] for t in rows),
                    'host MPI ABI changed')
        for node in {t['node'] for t in rows}:
            require(sum(t['verification'] == 'hashed' for t in rows if t['node'] == node) == 1,
                    'SIF must be hashed once per job/node')
    report.update(passed=True, partition=partition, job=(output / 'job.id').read_text().strip(),
                  runtime_sha256=request['runtime_sha256'], catalog_sha256=request['catalog_sha256'],
                  traces=len(traces), image_reads=dict(Counter(t['software'] for t in traces if t['verification']=='hashed')))
    if write:
        (output / 'verification.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'verify'))
    parser.add_argument('output', type=Path)
    parser.add_argument('--partition')
    parser.add_argument('--catalog', type=Path)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.output, args.partition, args.catalog)
    else:
        print(json.dumps(verify(args.output), sort_keys=True))
