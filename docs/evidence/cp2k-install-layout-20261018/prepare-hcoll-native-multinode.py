#!/usr/bin/env python3
"""Derive a two-node HCOLL-alias-free probe from the preserved one-node probe."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path('/home/stardust/sai-hpc-software')
OLD_NAME = 'hcoll-native-alias-free-16v100-20261018'
NEW_NAME = 'hcoll-native-alias-free-16v100-multinode-20261018'
original = ROOT / 'runtime-tests' / OLD_NAME
task = ROOT / 'runtime-tests' / NEW_NAME
assert not task.exists()
assert json.loads((original / 'results/native-alias-free.json').read_text())['passed'] is True
for path in (task / 'input', task / 'results', task / 'apptainer-runtime', task / 'apptainer-cache'):
    path.mkdir(parents=True)
for case in ('water-gpw', 'water-diag', 'water-elpa'):
    path = task / 'results' / case
    path.mkdir()
    (path / 'input.inp').write_bytes((original / 'results' / case / 'input.inp').read_bytes())
lock = json.loads((original / 'lock.json').read_text())
lock.update(prepared_at=datetime.now(timezone.utc).isoformat(), nodes=2, ranks=2,
            diagnostic_predecessor=str(original),
            scope='two-node native-export MPI execution in a private mount namespace at the canonical prefix; host runtime binds; HCOLL alias absent on both ranks; not physical installation acceptance')
sandbox = Path(lock['sandbox'])
(sandbox / 'home/stardust/sai-hpc-software/runtime/hc-native-16v-multi').mkdir(parents=True, exist_ok=True)


def replace_once(text, before, after):
    assert text.count(before) == 1, before
    return text.replace(before, after)


files = {}
for name in ('input/runner.py', 'input/native-rank.sh', 'native-exec.sh', 'job.sbatch'):
    content = (original / name).read_text()
    files[name] = dict(original_sha256=hashlib.sha256(content.encode()).hexdigest())
    content = content.replace(OLD_NAME, NEW_NAME).replace('/runtime/hc-native-16v', '/runtime/hc-native-16v-multi')
    if name == 'job.sbatch':
        content = replace_once(content, '#SBATCH --nodes=1', '#SBATCH --nodes=2')
        content = replace_once(content, '#SBATCH --ntasks=1', '#SBATCH --ntasks=2\n#SBATCH --ntasks-per-node=1')
    elif name == 'input/native-rank.sh':
        content = content.replace('/work/ldd.txt', '/work/ldd-${OMPI_COMM_WORLD_RANK}.txt')
        content = content.replace('/work/alias-absent.txt', '/work/alias-absent-${OMPI_COMM_WORLD_RANK}.txt')
        content = content.replace('LD_DEBUG_OUTPUT=/work/loader', 'LD_DEBUG_OUTPUT=/work/loader-${OMPI_COMM_WORLD_RANK}')
        content = replace_once(content, 'source /etc/profile.d/lmod.sh',
            'printf "%s\\t%s\\n" "$OMPI_COMM_WORLD_RANK" "$(hostname -s)" > /work/native-rank-${OMPI_COMM_WORLD_RANK}.tsv\n'
            'source /etc/profile.d/lmod.sh')
    elif name == 'input/runner.py':
        content = replace_once(content, "['mpirun', '-np', '1'", "['mpirun', '-np', '2'")
        content = replace_once(content, " / 'single'", " / 'multi'")
        content = replace_once(content, "probe / 'ldd.txt'", "probe / 'ldd-0.txt'")
        content = content.replace("folder.glob('loader.*')", "folder.glob('loader-*')")
        content = replace_once(content,
            "require((folder / 'alias-absent.txt').read_text().strip() == 'HCOLL_ALIAS_ABSENT', 'alias was visible')",
            "for rank in range(2):\n"
            "                require((folder / ('alias-absent-' + str(rank) + '.txt')).read_text().strip() == 'HCOLL_ALIAS_ABSENT', 'alias was visible')\n"
            "                traces = list(folder.glob('loader-' + str(rank) + '.*'))\n"
            "                require(len(traces) == 1, 'missing per-rank loader trace')\n"
            "                actual = re.findall(r'calling init:\\s*(\\S+)', traces[0].read_text(errors='replace'))\n"
            "                require(HPCX + '/hcoll/lib/libhcoll.so.1' in actual and not any('/opt/mellanox' in p for p in actual), 'wrong per-rank HCOLL load')\n"
            "            placements = [tuple((folder / ('native-rank-' + str(rank) + '.tsv')).read_text().strip().split('\\t')) for rank in range(2)]\n"
            "            require([p[0] for p in placements] == ['0', '1'] and len({p[1] for p in placements}) == 2, 'native MPI did not use two different nodes')")
        content = replace_once(content, 'alias_absent=True, installed_data_used=True)',
                               'alias_absent=True, installed_data_used=True, placements=placements)')
    (task / name).write_text(content)
    files[name]['derived_sha256'] = hashlib.sha256(content.encode()).hexdigest()
    if name.endswith('.sh') or name.endswith('.sbatch'):
        subprocess.run(['bash', '-n', str(task / name)], check=True)
(task / 'native-exec.sh').chmod(0o700)
compile((task / 'input/runner.py').read_text(), str(task / 'input/runner.py'), 'exec')
lock['derived_probe_files'] = files
(task / 'lock.json').write_text(json.dumps(lock, indent=2, sort_keys=True) + '\n')
job = subprocess.check_output(['sbatch', '--parsable', str(task / 'job.sbatch')], text=True).strip()
assert job.isdigit(), job
(task / 'job.id').write_text(job + '\n')
print(json.dumps(dict(task=str(task), job=job, nodes=2, ranks=2)))
