#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path
import re
import statistics
import sys

base = Path(sys.argv[1]).resolve()
summary = {'schema': 1, 'diagnostic_root': str(base), 'cases': [],
           'candidate_source_sha': '45651efd6db7da8738bc9d8d1f17e99259cabfe4',
           'baseline_version': '1.47.3', 'all_build_storage_in_home': True}
if (base / 'job.id').exists():
    summary['job_id'] = (base / 'job.id').read_text().strip()
for result_path in sorted(base.glob('*/result.json')):
    case = json.loads(result_path.read_text())
    folder = result_path.parent
    case['phases'] = [json.loads(line) for line in (folder / 'results/phases.jsonl').read_text().splitlines()] if (folder / 'results/phases.jsonl').exists() else []
    trace = folder / 'workload-fsync.trace'
    calls = []
    if trace.exists():
        for line in trace.read_text().splitlines():
            if not re.search(r'(?:fsync|fdatasync)(?:\(| resumed>)', line):
                continue
            match = re.search(r'^\s*\d+\s+(\d+\.\d+)\s+.*= (-?\d+).*<([0-9.]+)>$', line)
            if match:
                calls.append({'epoch': float(match[1]), 'returncode': int(match[2]), 'seconds': float(match[3])})
        case['fsync'] = {'calls': len(calls), 'total_seconds': sum(c['seconds'] for c in calls),
                         'errors': sum(c['returncode'] != 0 for c in calls)}
        for phase in case['phases']:
            end = phase['finished_epoch']
            phase_calls = [c for c in calls if end - phase['wall_seconds'] <= c['epoch'] <= end]
            phase['fsync_calls'] = len(phase_calls)
            phase['fsync_seconds'] = sum(c['seconds'] for c in phase_calls)
    case['hashes'] = {}
    for rel in ('run.log', 'fsck.log', 'verify.log', 'results/manifest.json', 'results/remount-verified.json', 'results/phases.jsonl', 'workload-fsync.trace', 'lock-holder.log', 'concurrent-mount.log', 'driver-fsync.trace', 'verify-syscalls.trace'):
        path = folder / rel
        if path.exists():
            case['hashes'][rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    summary['cases'].append(case)

manifests = [c['hashes']['results/manifest.json'] for c in summary['cases'] if 'results/manifest.json' in c['hashes']]
summary['all_completed_case_manifests_identical'] = bool(manifests) and len(set(manifests)) == 1
summary['comparisons'] = {}
for phase in ('create_64', 'rename_64', 'compile_32_j8', 'explicit_fsync'):
    by_variant = {}
    for case in summary['cases']:
        variant = case['label'].split('-')[0]
        for p in case['phases']:
            if p['phase'] == phase:
                by_variant.setdefault(variant, []).append(p['wall_seconds'])
    medians = {k: statistics.median(v) for k, v in by_variant.items()}
    summary['comparisons'][phase] = {'median_seconds': medians}
    if 'stock' in medians and 'upstream' in medians:
        summary['comparisons'][phase]['stock_over_upstream'] = medians['stock'] / medians['upstream']
driver = base / 'driver/fuse2fs-45651efd-ofd'
if driver.exists():
    summary['candidate_binary_sha256'] = hashlib.sha256(driver.read_bytes()).hexdigest()
summary['input_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in sorted((base / 'input').iterdir()) if p.is_file()}
text = json.dumps(summary, indent=2) + '\n'
(base / 'benchmark-summary.json').write_text(text)
print(text)
