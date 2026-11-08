#!/usr/bin/env python3
"""Time one build phase and sample the complete descendant process tree.

PSS sums shared pages proportionally; RSS sum double-counts shared pages.
Neither sampled peak includes the filesystem cache or guarantees a sub-second
peak. getrusage's maxrss is the largest individual child, not their sum.
"""
from collections import Counter
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time


def sample(root):
    rows = {}
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            text = (path / 'stat').read_text()
            fields = text[text.rindex(')') + 2:].split()
            rows[int(path.name)] = (int(fields[1]), text[text.index('(') + 1:text.rindex(')')],
                                   int(fields[11]) + int(fields[12]), int(fields[21]))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    selected = {root}
    while True:
        children = {pid for pid, row in rows.items() if row[0] in selected}
        if children <= selected:
            break
        selected |= children
    alive = selected & rows.keys()
    rss = sum(rows[pid][3] for pid in alive) * os.sysconf('SC_PAGE_SIZE') // 1024
    pss = 0
    unavailable = 0
    for pid in alive:
        try:
            fields = dict(line.split(':', 1) for line in (Path('/proc') / str(pid) / 'smaps_rollup').read_text().splitlines()[1:])
            pss += int(fields['Pss'].split()[0])
        except (FileNotFoundError, ProcessLookupError, PermissionError, KeyError):
            unavailable += 1
    return dict(processes=len(alive), commands=dict(Counter(rows[pid][1] for pid in alive)),
                sum_rss_kib=rss, sum_pss_kib=pss, pss_unavailable=unavailable,
                live_cpu_ticks=sum(rows[pid][2] for pid in alive))


def main():
    output = Path(sys.argv[1])
    argv = sys.argv[2:]
    if output.exists():
        raise ValueError('measurement already exists; use a new isolated run')
    start = time.monotonic()
    wall_start = time.time()
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    peak_rss = peak_pss = 0
    samples = 0
    with output.with_suffix('.samples.jsonl').open('x') as stream:
        process = subprocess.Popen(argv)
        while True:
            record = sample(process.pid)
            record['elapsed_seconds'] = time.monotonic() - start
            stream.write(json.dumps(record, sort_keys=True) + '\n')
            stream.flush()
            peak_rss = max(peak_rss, record['sum_rss_kib'])
            peak_pss = max(peak_pss, record['sum_pss_kib'])
            samples += 1
            try:
                code = process.wait(timeout=2)
                break
            except subprocess.TimeoutExpired:
                pass
    end = resource.getrusage(resource.RUSAGE_CHILDREN)
    seconds = time.monotonic() - start
    cpu = end.ru_utime + end.ru_stime - usage.ru_utime - usage.ru_stime
    record = dict(argv=argv, returncode=code, started_unix=wall_start,
                  elapsed_seconds=seconds, cpu_seconds=cpu, average_cpu_cores=cpu / seconds,
                  sampled_sum_rss_peak_kib=peak_rss, sampled_sum_pss_peak_kib=peak_pss,
                  largest_child_maxrss_kib=end.ru_maxrss, samples=samples,
                  affinity=sorted(os.sched_getaffinity(0)), generator=os.environ.get('SAI_BENCH_GENERATOR'),
                  build_jobs=os.environ.get('BUILD_JOBS'), job=os.environ.get('SAI_MD_ALLOCATED_JOB'),
                  node=os.environ.get('SAI_MD_ALLOCATED_NODE'))
    output.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
    print('BENCHMARK_PHASE ' + json.dumps(record, sort_keys=True), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
