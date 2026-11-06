"""Collect terminal communication diagnostics; a completed probe is not acceptance."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
from datetime import datetime, timezone

root = Path('/org/org-open-sai/proj-sai-hpc-software')
control = root / 'controller/devtools-application-20261105-h/controller'
sys.path.insert(0, str(control))
from cp2k_devtools_stage import recipe_files
from cp2k_acceptance import parse_science, compare_science, fixture_hashes

payloads, manifest, records = {}, {}, {}

def sha(data):
    return hashlib.sha256(data).hexdigest()

def retain(path):
    name = str(path.relative_to(root))
    raw = path.read_bytes()
    packed = gzip.compress(raw, mtime=0)
    payloads['raw/' + name + '.gz'] = packed
    manifest[name] = dict(bytes=len(raw), sha256=sha(raw), gzip_sha256=sha(packed))

cpu = json.loads((root / 'runs/cp2k-devtools-app-20261105-h-DSPRHBM/results/science/passed.json').read_text())['samples']['water64-gpw/2']
expected_binary = json.loads((root / 'runs/cp2k-devtools-app-20261105-c-4V100/results/science/water-gpw/1/execution.json').read_text())['binary_sha256']
for letter in ('j', 'k', 'l', 'm'):
    operation = root / ('operations/cp2k-devtools-application-20261105-comm-' + letter)
    run = root / ('runs/cp2k-devtools-app-20261105-comm-' + letter + '-4V100')
    request = json.loads((run / 'request.json').read_text())
    assert request['purpose'] == 'cp2k-communication-diagnostic-not-acceptance'
    assert recipe_files(request['controller']) == request['controller_files']
    assert sha((run / 'job.sbatch').read_bytes()) == request['script_sha256']
    for name, expected in request['diagnostic_files'].items():
        assert sha((operation / name).read_bytes()) == expected, name
    job = (run / 'job.id').read_text().strip()
    accounting = subprocess.check_output(['sacct', '-nP', '-j', job, '-o',
        'JobIDRaw,State,ExitCode,Elapsed,NodeList,AllocCPUS,AllocTRES'], text=True)
    row = [line.split('|') for line in accounting.splitlines() if line.split('|')[0] == job]
    assert len(row) == 1 and row[0][1:3] == ['COMPLETED', '0:0'], row
    preservation = json.loads((run / 'results/preservation.json').read_text())
    assert preservation['preserved'] and preservation['payload_exit_code'] == 0
    assert preservation['source_sha256'] == preservation['copy_sha256'] == request['source_sif_sha256']
    assert preservation['source_overlay_sha256'] == preservation['copy_overlay_sha256'] == request['source_overlay_sha256']
    assert sha(Path(request['source_sif']).read_bytes()) == sha((run / 'dependencies.sif').read_bytes()) == request['source_sif_sha256']
    assert Path(request['source_overlay']).stat().st_ino != (run / 'work.ext3').stat().st_ino
    lifecycle = [json.loads(p.read_text()) for p in (run / 'results').glob('managed-overlay-*.json')]
    assert len(lifecycle) == 1
    assert all(x['readonly'] and x['accepted'] and x['completed'] and not x['forced_stop']
               and x['unmount_returncode'] == x['driver_returncode'] == 0 for x in lifecycle)
    diagnostic = json.loads((run / 'results/diagnostic.json').read_text())
    assert diagnostic['diagnostic_only'] and not diagnostic['accepted']
    for name, sample in diagnostic['cases'].items():
        assert sample['binary_sha256'] == expected_binary
        folder = run / 'results' / name
        for filename, expected in fixture_hashes('water64-gpw').items():
            assert sha((folder / filename).read_bytes()) == expected
        if name == 'debugger':
            assert 'ptrace: Operation not permitted' in (folder / 'mpi.stderr').read_text()
            sample['cp2k_executed'] = False
            sample['classification'] = 'debugger permission failure; launcher exit is not a CP2K result'
        elif sample['returncode'] == 0:
            actual = parse_science((folder / 'cp2k.log').read_text(), (folder / 'forces.xyz').read_text(),
                                   str(sample['elapsed_seconds']), 'water64-gpw')
            assert actual == sample['sample']
            sample['against_cpu'] = compare_science(actual, cpu)
    records[letter] = dict(job_id=job, accounting=accounting, request=request,
                           preservation=preservation, lifecycle=lifecycle, diagnostic=diagnostic)
    for path in operation.iterdir():
        if path.is_file() and path.suffix in ('.c', '.py', '.sh', '.json', '.log'):
            retain(path)
    for name in ('request.json', 'job.sbatch', 'job.id'):
        retain(run / name)
    for path in (run / 'results').rglob('*'):
        if path.is_file() and path.suffix in ('.json', '.log', '.txt', '.stderr', '.inp', '.xyz'):
            retain(path)

operation = root / 'operations/cp2k-devtools-application-20261105-i'
for path in operation.glob('live-*.json'):
    retain(path)
summary = dict(captured_at=datetime.now(timezone.utc).isoformat(), accepted=False,
               published=False, records=records, raw_files=manifest)
payloads['summary.json'] = (json.dumps(summary, sort_keys=True, indent=2) + '\n').encode()
with tarfile.open(fileobj=sys.stdout.buffer, mode='w|gz') as archive:
    for name, data in sorted(payloads.items()):
        entry = tarfile.TarInfo(name)
        entry.size, entry.mode, entry.mtime = len(data), 0o644, 0
        archive.addfile(entry, io.BytesIO(data))
