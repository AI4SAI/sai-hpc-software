"""Verify the real patched CP2K build and full science, then emit its raw archive."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
from datetime import datetime, timezone

root = Path('/org/org-open-sai/proj-sai-hpc-software')
run = root / 'runs/cp2k-devtools-app-20261105-n-4V100'
control = root / 'controller/devtools-application-20261105-n/controller'
sys.path.insert(0, str(control))
from cp2k_devtools_stage import recipe_files
from cp2k_acceptance import CASES, fixture_hashes, parse_science, compare_science
from software_controller import recipe_fingerprint
from cp2k_source_fixes import NCCL_SOURCE_SHA256, NCCL_REPAIRED_SHA256

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def read(path):
    return json.loads(path.read_text())

request = read(run / 'request.json')
assert recipe_files(control) == request['controller_files']
assert recipe_fingerprint('cp2k', control) == request['identity']['recipe_sha256']
assert sha((run / 'job.sbatch').read_bytes()) == request['script_sha256']
job = (run / 'job.id').read_text().strip()
accounting = subprocess.check_output(['sacct', '-nP', '-j', job, '-o',
    'JobIDRaw,State,ExitCode,Elapsed,NodeList,AllocCPUS,AllocTRES'], text=True)
row = [line.split('|') for line in accounting.splitlines() if line.split('|')[0] == job]
assert len(row) == 1 and row[0][1:3] == ['COMPLETED', '0:0'], row
assert 'gres/gpu=2' in row[0][6]
installation = read(run / 'results/install-success.json')
assert installation['layout'] == 'shared-devtools'
assert installation['shared_provider']['contract_sha256'] == request['contract_sha256']
assert installation['install_prefix'] == request['identity']['install_prefix']
preservation = read(run / 'results/preservation.json')
assert preservation['preserved'] and preservation['payload_exit_code'] == 0
expected_sif = request['source_sif_sha256']
assert preservation['source_sha256'] == preservation['copy_sha256'] == expected_sif
assert sha(Path(request['source_sif']).read_bytes()) == sha((run / 'dependencies.sif').read_bytes()) == expected_sif
assert Path(request['source_sif']).stat().st_ino != (run / 'dependencies.sif').stat().st_ino
assert (run / 'work.ext3').stat().st_mode & 0o777 == 0o400
lifecycle = [read(p) for p in (run / 'results').glob('managed-overlay-*.json')]
assert len(lifecycle) == 2 and {p['readonly'] for p in lifecycle} == {False, True}
assert all(p['completed'] and p['accepted'] and not p['forced_stop'] and
           p['driver_returncode'] == p['unmount_returncode'] == 0 for p in lifecycle)
repair = read(run / 'results/nccl-mpi-progress-fix.json')
assert repair['applied'] and repair['bootstrap_sites'] == 3
assert repair['before_sha256'] == NCCL_SOURCE_SHA256 and repair['after_sha256'] == NCCL_REPAIRED_SHA256
checks = json.loads((run / 'results/application-checks.log').read_text().splitlines()[-1])
assert checks['artifact_checks']
binary_sha = checks['elf']['bin/cp2k.psmp']['sha256']
passed = read(run / 'results/science/passed.json')
assert passed['single_node'] and not passed['multinode_verified'] and not passed['published']
assert len(passed['samples']) == 14 and len(passed['comparisons']) == 7
cpu_path = root / 'runs/cp2k-devtools-app-20261105-h-DSPRHBM/results/science/passed.json'
cpu = read(cpu_path)['samples']
comparisons, against_cpu = {}, {}
for case in CASES:
    for ranks in (1, 2):
        key = case + '/' + str(ranks)
        directory = run / 'results/science' / key
        execution = read(directory / 'execution.json')
        assert execution['returncode'] == 0 and execution['mpi_ranks'] == ranks
        assert execution['binary_sha256'] == binary_sha
        for name, expected in fixture_hashes(case).items():
            assert sha((directory / name).read_bytes()) == expected
        actual = parse_science((directory / 'cp2k.log').read_text(),
                               (directory / 'forces.xyz').read_text(),
                               str(execution['elapsed_seconds']), case)
        assert actual == passed['samples'][key]
        against_cpu[key] = compare_science(actual, cpu[key])
    comparisons[case] = compare_science(passed['samples'][case + '/2'], passed['samples'][case + '/1'])
    assert comparisons[case] == passed['comparisons'][case]
nccl = '\n'.join(line for p in (run / 'results/science/water64-gpw/2').glob('nccl.*.log')
                 for line in p.read_text().splitlines() if 'Init COMPLETE' in line and 'ncclCommInitRank' in line)
devices = sorted(set(re.findall(r' rank (\d+) nranks 2 cudaDev (\d+) nvmlDev \d+ busId ([0-9a-f]+)', nccl)))
assert {row[0] for row in devices} == {'0', '1'} and len({row[2] for row in devices}) == 2
earlier_path = root / 'operations/cp2k-devtools-application-20261105-evidence-partial-r3/evidence.tar.gz'
earlier_bytes = earlier_path.read_bytes()
assert sha(earlier_bytes) == '7a16b9c47d7b1c7eb1f5955b84068e9704b079ac25a66f5e720bdfd7206fd5b9'
with tarfile.open(fileobj=io.BytesIO(earlier_bytes), mode='r:gz') as archive:
    earlier = json.loads(archive.extractfile('summary.json').read())
assert set(checks['flags']) == set(earlier['records']['4V100']['build']['artifact_checks']['flags'])
old_checks_path = root / 'runs/cp2k-devtools-app-20261105-c-4V100/results/application-checks.log'
assert sha(old_checks_path.read_bytes()) == earlier['raw_files'][str(old_checks_path.relative_to(root / 'runs'))]['sha256']
old_checks = json.loads(old_checks_path.read_text().splitlines()[-1])
core_libraries = {name: value['sha256'] for name, value in checks['elf'].items()
                  if Path(name).name.startswith('libcp2k.so')}
assert core_libraries and all(value != old_checks['elf'][name]['sha256']
                              for name, value in core_libraries.items())
cpu_key = str(cpu_path.relative_to(root / 'runs'))
assert sha(cpu_path.read_bytes()) == earlier['raw_files'][cpu_key]['sha256']
payloads, manifest = {}, {}
paths = {run / name for name in ('request.json', 'job.sbatch', 'job.id')}
paths.update(p for p in (run / 'results').rglob('*')
             if p.is_file() and p.suffix in ('.json', '.log', '.txt', '.stderr', '.inp', '.xyz', '.yaml', '.lock'))
paths.add(control / 'cp2k_source_fixes.py')
for operation in ('cp2k-devtools-application-20261105-n', 'cp2k-devtools-application-20261105-n-evidence'):
    paths.update(p for p in (root / 'operations' / operation).iterdir()
                 if p.is_file() and p.suffix in ('.py', '.json', '.log'))
for path in sorted(paths):
    name = str(path.relative_to(root))
    raw = path.read_bytes()
    packed = gzip.compress(raw, mtime=0)
    payloads['raw/' + name + '.gz'] = packed
    manifest[name] = dict(bytes=len(raw), sha256=sha(raw), gzip_sha256=sha(packed))
summary = dict(captured_at=datetime.now(timezone.utc).isoformat(), accepted=True,
    request=request, accounting=accounting, installation=installation,
    preservation=preservation, lifecycle=lifecycle, source_repair=repair,
    binary_sha256=binary_sha, core_library_sha256=core_libraries,
    flags=checks['flags'], scientific_samples=14,
    rank_comparisons=comparisons, against_cpu=against_cpu, nccl_devices=devices,
    earlier_three_partition_archive_sha256=sha(earlier_bytes),
    original_recipe_sha256=earlier['records']['4V100']['build']['request']['identity']['recipe_sha256'],
    all_partitions_accepted=True, recipes_identical_across_partitions=False,
    published=False, physical_installation=False, multinode_verified=False,
    raw_files=manifest)
payloads['summary.json'] = (json.dumps(summary, sort_keys=True, indent=2) + '\n').encode()
with tarfile.open(fileobj=sys.stdout.buffer, mode='w|gz') as archive:
    for name, data in sorted(payloads.items()):
        entry = tarfile.TarInfo(name)
        entry.size, entry.mode, entry.mtime = len(data), 0o644, 0
        archive.addfile(entry, io.BytesIO(data))
