#!/usr/bin/env python3
"""Hash the archive and reparse each run using its own frozen controller."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
RAW = HERE / 'raw'


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def routine_timings(root):
    """Retain diagnostic timers without subtracting them from wall time."""
    names = {'CP2K', 'write_mo_set_to_restart', 'jit_kernel_transpose',
             'jit_kernel_multiply', 'cp_fm_diag_elpa', 'cp_fm_diag_elpa_base',
             'pbe_lda_eval', 'grid_collocate_task_list', 'grid_integrate_task_list'}
    result = {}
    for path in sorted((root / 'cases').glob('water*/*/*/cp2k.log')):
        text = path.read_text()
        assert 'T I M I N G' in text
        text = text[text.rfind('T I M I N G'):]
        rows = re.findall(r'^\s*(\S+)\s+(\d+)\s+[\d.]+\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*$', text, re.M)
        result[str(path.parent.relative_to(root / 'cases'))] = {
            name: dict(calls_max=int(calls), self_max_seconds=float(self_max),
                       total_max_seconds=float(total_max))
            for name, calls, self_avg, self_max, total_avg, total_max in rows if name in names}
    return result


def special(row):
    root = RAW / row['label']
    sys.path.insert(0, str(root / 'control/deployment/cp2k-container-overhead'))
    import run as common
    request = json.loads((root / 'request.json').read_text())
    reference = json.loads((root / 'reference.json').read_text())
    job = (root / 'job.id').read_text().strip()
    fields = next(line.split('|') for line in (root / 'results/accounting.txt').read_text().splitlines()
                  if line.startswith(job + '|'))
    assert fields[1:3] == [row['partition'], row['state']]
    assert request['uid'] != 0 and request['user'] == 'stardust'
    result = dict(job=job, partition=fields[1], state=fields[2], exit_code=fields[3], node=fields[5])
    if row['kind'] == 'failed-native':
        assert fields[3] == '1:0'
        for name, digest in request['inputs'].items():
            assert checksum(root / name) == digest, name
        common.check_probes(root, request)
        for repeat, mode in common.schedule('version', common.VERSION_REPEATS):
            common.parse(root / 'cases/version' / repeat / mode, 'version', reference)
        failed = root / 'cases/water-gpw/warmup/native'
        log = (failed / 'cp2k.log').read_text()
        assert 'SIGSEGV' in log and 'jit_transpose_handle' in log and 'libsmm_acc.cpp:397' in log
        assert not (failed / 'elapsed.txt').exists()
        assert not (root / 'cases/water-gpw/warmup/sif').exists()
        result.update(provenance_passed=True, completed_version_calls=24,
                      failed_route='native', failure='SIGSEGV in DBCSR transpose JIT; no SIF science was run')
    else:
        assert row['kind'] == 'diagnostic' and fields[3] == '0:0'
        submission = json.loads((root / 'submission.json').read_text())
        for name, digest in submission['inputs'].items():
            assert checksum(root / name) == digest, name
        records = json.loads((root / 'results/diagnosis.json').read_text())
        expected = [(threads, stack, mode) for threads, stack in [(4, None), (4, '256M'), (1, None)]
                    for mode in ['native', 'sif']]
        assert [(r['threads'], r['stack'], r['mode']) for r in records] == expected
        for record in records:
            folder = root / record['label']
            assert checksum(folder / 'input.inp') == request['inputs']['fixtures/water-gpw.inp']
            rank = (folder / 'rank.tsv').read_text().strip().split('\t')
            assert rank[0] == str(request['uid']) and rank[1] == fields[5] and rank[4] == str(record['threads'])
            if record['exit_code'] == 0:
                assert common.parse(folder, 'water-gpw', reference) == record['science']
            else:
                assert 'SIGSEGV' in (folder / 'cp2k.log').read_text()
        result['samples'] = records
    return result


manifest = json.loads((RAW / 'manifest.json').read_text())
if len(sys.argv) == 2:
    row = next(r for r in manifest['runs'] if r['label'] == sys.argv[1])
    print(json.dumps(special(row), sort_keys=True))
else:
    hashes = json.loads((RAW / 'SHA256.json').read_text())
    assert {str(p.relative_to(RAW)) for p in RAW.rglob('*') if p.is_file()} == set(hashes) | {'SHA256.json'}
    for name, digest in hashes.items():
        assert checksum(RAW / name) == digest, name
    results = {}
    science_verifier = RAW / 'verifier/deployment/cp2k-container-overhead/run.py'
    assert checksum(science_verifier) == manifest['science_verifier']['sha256']
    for row in manifest['runs']:
        root = RAW / row['label']
        if row['kind'] == 'science':
            for name in ['cp2k_acceptance.py', 'cp2k_benchmark.py', 'source_cache.py']:
                assert checksum(RAW / 'verifier/controller' / name) == checksum(root / 'control/controller' / name)
            command = [sys.executable, '-B', str(science_verifier), 'verify', str(root)]
        elif row['kind'] == 'startup':
            command = [sys.executable, '-B', str(root / 'control/deployment/cp2k-container-overhead/startup.py'), 'verify', str(root)]
        else:
            command = [sys.executable, '-B', __file__, row['label']]
        result = json.loads(subprocess.check_output(command, text=True))
        assert result['partition'] == row['partition'] and result['state'] == row['state']
        if row['kind'] == 'science':
            result['routine_timings'] = routine_timings(root)
        results[row['label']] = result
    print(json.dumps(dict(passed=True, files_hashed=len(hashes), runs=results,
                          scope='Raw-log numerical and provenance revalidation; timings retain observed variability'),
                     indent=2, sort_keys=True))
