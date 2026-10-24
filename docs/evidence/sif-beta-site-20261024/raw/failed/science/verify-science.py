import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, '/home/stardust/sai-hpc-software/development/sif-beta-site-20261024-r1/controller')
from cp2k_benchmark import parse_science, compare_science
from abacus_benchmark import parse_scf
from gpumd_science import xyz, compare

root = Path.cwd()
old = root.parent / 'sif-lmod-20261024-16V100-r4'
plan = json.loads((old / 'request.json').read_text())['plan']
report = {}
folder = root / 'cp2k'
science = parse_science((folder / 'output.out').read_text(), (folder / 'forces.xyz').read_text(),
                        (folder / 'elapsed.txt').read_text(), 'water-gpw')
report['cp2k'] = compare_science(plan['cp2k_reference'], science)
folder = root / 'abacus'
sample = parse_scf((folder / 'abacus.out').read_text(), (folder / 'OUT.autotest/running_scf.log').read_text(),
                   (folder / 'elapsed.txt').read_text())
error = abs(sample['energy_ev'] - plan['abacus_energy_ev'])
assert error < 1e-5, sample
report['abacus'] = dict(energy_error_ev=error, **sample)
folder = root / 'lammps'
log = (folder / 'stdout.txt').read_text()
assert '(Mixed Precision)' in log and '- Using acceleration for' in log
[line] = re.findall(r'^SAI_GPU_OBS (.+)$', log, re.M)
values = [float(v) for v in line.split()]
dump = (folder / 'result.dump').read_text().split('ITEM: ATOMS id fx fy fz\n')[1]
values.extend(float(v) for row in dump.splitlines() for v in row.split()[1:])
expected = [v for group in plan['lammps_reference'] for v in group]
assert len(values) == len(expected)
assert all(math.isfinite(a) and abs(a-b) <= 2e-6 + 2e-5*abs(b) for a,b in zip(values, expected))
report['lammps'] = dict(max_error=max(abs(a-b) for a,b in zip(values,expected)), values=len(values))
e, f = xyz(root / 'gpumd/dump.xyz')
ge, gf = xyz(root / 'gpumd/gold.xyz')
report['gpumd'] = dict(energy_error=compare([[e]], [[ge]], 1e-3, 'GPUMD energy'),
                       force_error=compare(f, gf, 1e-3, 'GPUMD forces'))
catalog = json.loads(Path('/home/stardust/sai-hpc-software/deployment/sif-development-20261024/payload/catalog.json').read_text())
traces = [json.loads(p.read_text()) for p in (root / 'trace').glob('*.json')]
for app in report:
    rows = [t for t in traces if t['software'] == app]
    assert rows and all(t['sha256'] == catalog['applications'][app]['partitions']['16V100']['sha256'] for t in rows)
    if app != 'gpumd':
        assert {t['rank'] for t in rows} == {'0','1'}
        assert all(t['mpi'] == catalog['applications'][app]['partitions']['16V100']['mpi_prefix'] for t in rows)
        log = (root / app / 'stdout.txt').read_text()
        assert 'MAP_OPT=ppr:2:node:pe=4' in log
        assert not re.search(r'UCX\s+ERROR|unable to create shared memory|performance degradation', log, re.I)
report.update(passed=True, job=(root / 'job.id').read_text().strip(), partition='16V100', ranks=2, gpus=2,
              runtime_sha256=catalog['runtime_sha256'])
(root / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
