"""Read-only collection of completed CP2K integration evidence; tar on stdout."""
import gzip
import hashlib
import io
import json
import re
from pathlib import Path
import subprocess
import sys
import tarfile
from datetime import datetime, timezone

partial = sys.argv[1:] == ['--partial']
assert not sys.argv[1:] or partial, 'Unexpected collection option'

project = Path('/org/org-open-sai/proj-sai-hpc-software')
control = project / 'controller/devtools-application-20261105-h/controller'
sys.path.insert(0, str(control))
from cp2k_devtools_stage import recipe_files
from cp2k_acceptance import CASES, parse_science, compare_science, fixture_hashes


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(path.read_text())


payloads, manifest, records = [], {}, {}


def retain(path, name):
    raw = path.read_bytes()
    compressed = gzip.compress(raw, mtime=0)
    payloads.append(('raw/' + name + '.gz', compressed))
    manifest[name] = dict(source=str(path), bytes=len(raw), sha256=digest(raw),
                          gzip_sha256=digest(compressed))


for partition in ('DSPRHBM', '16V100', '4V100', '8V100V0'):
    records[partition] = {}
    final_letter = {'DSPRHBM': 'h', '4V100': 'i', '16V100': 'g', '8V100V0': 'g'}[partition]
    phases = [('build', 'c')]
    if not (partial and partition == '4V100'):
        phases.append(('reverify', final_letter))
    for phase, letter in phases:
        run = project / 'runs' / ('cp2k-devtools-app-20261105-' + letter + '-' + partition)
        request = read_json(run / 'request.json')
        job = (run / 'job.id').read_text().strip()
        raw_accounting = subprocess.check_output(
            ['sacct', '-X', '-nP', '-j', job, '-o', 'JobIDRaw,State,ExitCode,Elapsed,NodeList,AllocCPUS,AllocTRES'], text=True)
        rows = [row.split('|') for row in raw_accounting.splitlines() if row.split('|')[0] == job]
        assert len(rows) == 1, (job, rows)
        fields = ('job_id', 'state', 'exit_code', 'elapsed', 'nodes', 'allocated_cpus', 'allocated_tres')
        accounting = dict(zip(fields, rows[0]))
        assert accounting['state'] == ('FAILED' if phase == 'build' else 'COMPLETED'), accounting
        assert accounting['exit_code'] == ('1:0' if phase == 'build' else '0:0'), accounting
        assert recipe_files(request['controller']) == request['controller_files'], 'controller changed'
        assert digest((run / 'job.sbatch').read_bytes()) == request['script_sha256'], 'batch script changed'
        checks = json.loads((run / 'results/application-checks.log').read_text().splitlines()[-1])
        assert checks['artifact_checks'] is True
        lifecycle = [read_json(path) for path in sorted((run / 'results').glob('managed-overlay-*.json'))]
        assert lifecycle and all(x['completed'] and not x['forced_stop'] and
                                  x['unmount_returncode'] == x['driver_returncode'] == 0 for x in lifecycle)
        if phase == 'build':
            writable = [x for x in lifecycle if x.get('readonly') is False]
            assert len(writable) == 1 and writable[0]['accepted']
            reused = read_json(run / 'results/install-success.json')
            assert reused['layout'] == 'shared-devtools'
            assert reused['shared_provider']['contract_sha256'] == request['contract_sha256']
            assert reused['install_prefix'] == request['identity']['install_prefix']
        else:
            assert len(lifecycle) == 1 and lifecycle[0]['readonly'] and lifecycle[0]['accepted']
        preservation = read_json(run / 'results/preservation.json')
        assert preservation['preserved']
        assert preservation['source_sha256'] == preservation['copy_sha256'] == request['source_sif_sha256']
        assert digest(Path(request['source_sif']).read_bytes()) == request['source_sif_sha256']
        assert digest((run / 'dependencies.sif').read_bytes()) == request['source_sif_sha256']
        checked = {key: value for key, value in checks.items() if key != 'elf'}
        checked['elf_count'] = len(checks['elf'])
        checked['binary_sha256'] = checks['elf']['bin/cp2k.psmp']['sha256']
        record = dict(run_id=run.name, request=request, accounting=accounting,
                      artifact_checks=checked, lifecycle=lifecycle, preservation=preservation)
        if phase == 'build':
            record['shared_devtools_contract_sha256'] = reused['shared_provider']['contract_sha256']
            record['build_phase_seconds'] = writable[0]['finished'] - writable[0]['started']
        if phase == 'reverify':
            node_profile = run / 'results/verification-node.json'
            if node_profile.exists():
                assert read_json(node_profile)['native_flags_match'] is True
            assert preservation['source_overlay_sha256'] == preservation['copy_overlay_sha256'] == request['source_overlay_sha256']
            copied = read_json(run / 'results/copied-application.json')
            assert copied['recompiled'] is False and copied['sha256'] == request['source_overlay_sha256']
            original_overlay = Path(request['source_overlay'])
            copied_overlay = run / 'work.ext3'
            assert original_overlay.stat().st_ino != copied_overlay.stat().st_ino
            assert (original_overlay.stat().st_mode & 0o777) == (copied_overlay.stat().st_mode & 0o777) == 0o400
            passed = read_json(run / 'results/science/passed.json')
            assert len(passed['samples']) == 14 and len(passed['comparisons']) == 7
            assert passed['single_node'] is True and passed['multinode_verified'] is False and passed['published'] is False
            samples, comparisons, binaries = {}, {}, set()
            for case in CASES:
                for ranks in (1, 2):
                    key = case + '/' + str(ranks)
                    directory = run / 'results/science' / key
                    execution = read_json(directory / 'execution.json')
                    assert execution['returncode'] == 0 and execution['mpi_ranks'] == ranks
                    for name, expected in fixture_hashes(case).items():
                        assert digest((directory / name).read_bytes()) == expected, (key, name)
                    binaries.add(execution['binary_sha256'])
                    sample = parse_science((directory / 'cp2k.log').read_text(),
                                           (directory / 'forces.xyz').read_text(),
                                           str(execution['elapsed_seconds']), case)
                    assert sample == passed['samples'][key]
                    samples[key] = sample
                comparisons[case] = compare_science(samples[case + '/2'], samples[case + '/1'])
                assert comparisons[case] == passed['comparisons'][case]
            assert len(binaries) == 1
            original = project / 'runs' / request['source_application_run']
            assert digest((original / 'request.json').read_bytes()) == request['source_request_sha256']
            original_binary = read_json(original / 'results/science/water-gpw/1/execution.json')['binary_sha256']
            assert binaries == {original_binary}
            nccl = []
            for path in sorted((run / 'results/science/water64-gpw/2').glob('nccl.*.log')):
                nccl.extend(line for line in path.read_text().splitlines()
                            if 'ncclCommInitRank' in line and 'nranks 2' in line and 'Init COMPLETE' in line)
            devices = sorted(set(re.findall(r' rank (\d+) nranks 2 cudaDev (\d+) nvmlDev \d+ busId ([0-9a-f]+)', '\n'.join(nccl))))
            if partition != 'DSPRHBM':
                assert 'gres/gpu=2' in accounting['allocated_tres']
                assert {row[0] for row in devices} == {'0', '1'} and len({row[2] for row in devices}) == 2
            record.update(copied_application=copied, binary_sha256=original_binary,
                          scientific_samples=14, comparisons=comparisons,
                          nccl_devices=[dict(rank=int(rank), cuda_device=int(device), bus_id=bus)
                                        for rank, device, bus in devices])
        records[partition][phase] = record
        files = {run / name for name in ('request.json', 'job.sbatch', 'job.id')}
        for path in (run / 'results').iterdir():
            if path.is_file() and path.suffix in ('.json', '.log', '.txt', '.yaml', '.lock'):
                files.add(path)
        for path in (run / 'results/science').rglob('*'):
            if path.is_file() and path.suffix in ('.json', '.log', '.stderr', '.inp', '.xyz'):
                files.add(path)
        for path in sorted(files):
            retain(path, run.name + '/' + str(path.relative_to(run)))

earlier = project / 'operations/cp2k-devtools-application-20261105-c/earlier-sif-preservation.json'
retain(earlier, 'earlier-sif-preservation.json')
operation = project / 'operations/cp2k-devtools-application-20261105-f'
for name in ('rescheduling.json', 'diagnostic-steps.json', 'diagnostic-outcome.json', 'node-profile.json'):
    retain(operation / name, 'operations/' + name)
for letter in ('g', 'h'):
    for path in sorted((project / 'operations' / ('cp2k-devtools-application-20261105-' + letter)).glob('source-preservation-*.json')):
        receipt = read_json(path)
        assert receipt['observed_sparse_sha256'] == receipt['expected_full_file_sha256'] and receipt['preserved']
        retain(path, 'operations/' + letter + '/' + path.name)
retain(project / 'operations/cp2k-devtools-application-20261105-h/rescheduling.json',
       'operations/h/rescheduling.json')
retain(project / 'operations/cp2k-devtools-application-20261105-i/reason.json',
       'operations/i/reason.json')
unsuccessful_attempts = {}
for suffix in ('d-DSPRHBM', 'e-16V100', 'e-4V100', 'e-8V100V0', 'f-16V100', 'g-4V100', 'h-4V100',
               *(['i-4V100'] if partial else [])):
    cancelled = project / 'runs' / ('cp2k-devtools-app-20261105-' + suffix)
    job = (cancelled / 'job.id').read_text().strip()
    accounting = subprocess.check_output(
        ['sacct', '-nP', '-j', job, '-o', 'JobIDRaw,State,ExitCode,DerivedExitCode,Elapsed,NodeList'], text=True)
    assert ('FAILED' if suffix in ('d-DSPRHBM', 'h-4V100', 'i-4V100') else 'CANCELLED') in accounting
    unsuccessful_attempts[suffix] = dict(accounting=accounting)
    for name in ('request.json', 'job.sbatch', 'job.id'):
        retain(cancelled / name, cancelled.name + '/' + name)
    for path in sorted((cancelled / 'results').rglob('*')):
        if path.is_file() and path.suffix in ('.json', '.log', '.stderr', '.inp', '.xyz', '.txt'):
            retain(path, cancelled.name + '/' + str(path.relative_to(cancelled)))
    if (cancelled / 'dependencies.sif').exists():
        request = read_json(cancelled / 'request.json')
        assert digest((cancelled / 'dependencies.sif').read_bytes()) == request['source_sif_sha256']
        unsuccessful_attempts[suffix]['dependency_copy_preserved'] = True
cpu = read_json(project / 'runs' / records['DSPRHBM']['reverify']['run_id'] / 'results/science/passed.json')['samples']
cross_partition = {}
for partition in ('16V100', '4V100', '8V100V0'):
    if partial and partition == '4V100':
        continue
    gpu = read_json(project / 'runs' / records[partition]['reverify']['run_id'] / 'results/science/passed.json')['samples']
    cross_partition[partition] = {key: compare_science(gpu[key], cpu[key]) for key in cpu}
summary = dict(captured_at=datetime.now(timezone.utc).isoformat(), records=records,
               scope='CP2K v2026.2 compilation and single-node 1/2-rank science using copied dependency SIFs',
               all_partitions_accepted=not partial,
               accepted_partitions=[p for p in records if 'reverify' in records[p]],
               published=False, physical_installation=False, multinode_verified=False,
               unsuccessful_attempts=unsuccessful_attempts,
               source_commit='67b5da876dd6a76b8b021d5a04d1c81ba79a4c50',
               fixture_hashes={case: fixture_hashes(case) for case in CASES},
               cross_partition_against_cpu=cross_partition, raw_files=manifest)
payloads.append(('summary.json', (json.dumps(summary, sort_keys=True, indent=2) + '\n').encode()))
with tarfile.open(fileobj=sys.stdout.buffer, mode='w|gz') as archive:
    for name, content in payloads:
        entry = tarfile.TarInfo(name)
        entry.size, entry.mode, entry.mtime = len(content), 0o644, 0
        archive.addfile(entry, io.BytesIO(content))
